"""
Обработка отдельных изображений (не PDF) через AI-конвейер → Excel-смета.

Использование:
    python process_images.py image1.jpg image2.png ...

На каждую картинку:
1. Кэш признания (recognition_cache): повторные прогоны не перебрасывают кости —
   подтверждённый результат берётся из кэша БЕЗ вызова vision.
2. Протокол стабильности (recognition_protocol): до 3 попыток единого анализа
   (UNIFIED_PROMPT_V2, return_meta=True), масштабный чек + голосование;
   если размеры не сошлись — fallback на средние общепринятые размеры
   (build_standard_modules), масштабированные на всю мебель.
3. analyze_drawing — legacy-fallback если 0 модулей (кроме no-door-зон).
4. Заполнение шаблона Excel «Таблица для расчетов пустая.xlsx».
"""

import asyncio
import sys
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-7s %(message)s',
    datefmt='%H:%M:%S'
)

from app.services.image_analyzer import GeminiImageAnalyzer, UNIFIED_PROMPT_V2
from app.services.recognition_protocol import (
    build_standard_modules,
    choose_attempt,
    door_count_of,
    door_counts_by_zone,
    plausible_total,
)
from app.services import recognition_cache
from app.services.full_pipeline import (
    PipelineResult,
    RoomSpec,
    _calculate_quality,
    is_no_door_zone_page,
)
from app.services.template_filler import fill_template_from_pipeline

TEMPLATE = ROOT / "templates" / "Таблица для расчетов пустая.xlsx"
OUTPUT_DIR = ROOT / "output"

CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def _conf_rank(confidence) -> int:
    return CONF_RANK.get(confidence or "", 0)


def _module_sum(modules) -> int:
    """Сумма ширин модулей с учётом quantity."""
    return sum((m.width or 0) * max(m.quantity or 1, 1) for m in modules)


def _attempt_better(b: dict, a: dict) -> bool:
    """Попытка b лучше a: scale_ok важнее, затем confidence, затем |sum−total|."""
    if bool(b.get("scale_ok")) != bool(a.get("scale_ok")):
        return bool(b.get("scale_ok"))
    rb, ra = _conf_rank(b.get("confidence")), _conf_rank(a.get("confidence"))
    if rb != ra:
        return rb > ra
    db = abs(_module_sum(b.get("modules") or []) - (b.get("total_width_mm") or 0))
    da = abs(_module_sum(a.get("modules") or []) - (a.get("total_width_mm") or 0))
    return db < da


def _attempt_from_cache(cached: dict) -> dict:
    """Неподтверждённая запись кэша → первая попытка протокола."""
    d = cached.get("data") or {}
    modules = recognition_cache.dicts_to_modules(d.get("modules") or [])
    return {
        "modules": modules,
        "zone_type": d.get("zone_type"),
        "materials": d.get("materials") or [],
        "confidence": d.get("confidence", "low"),
        "total_width_mm": int(d.get("total_width_mm") or 0),
        "scale_ok": bool(d.get("scale_ok", False)),
        "door_count": int(d.get("door_count") or 0) or door_count_of(modules),
    }


def _data_for_cache(chosen: dict, modules=None) -> dict:
    """Результат признания → data-блок для recognition_cache.save()."""
    mods = chosen.get("modules") if modules is None else modules
    return {
        "modules": [recognition_cache.module_to_dict(m) for m in (mods or [])],
        "zone_type": chosen.get("zone_type"),
        "materials": list(chosen.get("materials") or []),
        "confidence": chosen.get("confidence", "low"),
        "total_width_mm": int(chosen.get("total_width_mm") or 0),
        "scale_ok": bool(chosen.get("scale_ok")),
        "door_count": int(chosen.get("door_count") or 0) or door_count_of(mods or []),
    }


def _log_attempt(n: int, attempt: dict, source: str = ""):
    print(
        f"   🔄 Попытка {n}{source}: total={attempt.get('total_width_mm') or 0}мм, "
        f"scale_ok={attempt.get('scale_ok')}, conf={attempt.get('confidence')}, "
        f"дверей={attempt.get('door_count') or door_count_of(attempt.get('modules') or [])}"
    )


async def process_images(image_paths: list[str], output_name: str = "Расчет") -> str:
    """
    Обработать список изображений и заполнить Excel.
    
    Args:
        image_paths: пути к файлам изображений
        output_name: имя выходного файла (без .xlsx)
    
    Returns:
        путь к созданному Excel-файлу
    """
    if not TEMPLATE.exists():
        raise FileNotFoundError(f"Шаблон не найден: {TEMPLATE}")
    
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    analyzer = GeminiImageAnalyzer()
    
    try:
        # ── Шаг 1: Анализ каждой картинки ──
        rooms = []
        errors = []
        no_door_pages = []  # страницы «не корпусная мебель» (без дверец)
        
        for i, img_path in enumerate(image_paths, 1):
            path = Path(img_path)
            if not path.exists():
                errors.append(f"Файл не найден: {img_path}")
                print(f"❌ [{i}/{len(image_paths)}] {path.name} — файл не найден")
                continue
            
            print(f"\n🖼️  [{i}/{len(image_paths)}] Анализ: {path.name} ({path.stat().st_size // 1024} KB)")
            print("   " + "─" * 60)
            
            modules, zone_type, materials, confidence = [], None, [], "low"
            method = ""
            vision_calls = 0  # число реальных vision-вызовов (для attempts в кэше)

            async def vision_attempt() -> dict:
                """Одна попытка единого анализа с метаданными протокола."""
                result = await analyzer.analyze_page(path, return_meta=True)
                m, z, mat, conf, meta = result
                return {
                    "modules": m,
                    "zone_type": z,
                    "materials": mat,
                    "confidence": conf,
                    "total_width_mm": int(meta.get("total_width_mm") or 0),
                    "scale_ok": bool(meta.get("scale_ok")),
                    "door_count": int(meta.get("door_count") or 0),
                }
            
            # ── 0. Кэш признания ──
            cached = recognition_cache.load(path, UNIFIED_PROMPT_V2)
            
            if cached and cached.get("approved"):
                # 💾 Подтверждённый кэш: vision НЕ вызываем вовсе
                data = cached.get("data") or {}
                modules = recognition_cache.dicts_to_modules(data.get("modules") or [])
                zone_type = data.get("zone_type")
                materials = data.get("materials") or []
                confidence = data.get("confidence", "low")
                method = "кэш признания (подтверждено)"
                print(
                    f"   💾 Кэш признания (подтверждено) — vision не вызываем "
                    f"(attempts={cached.get('attempts')}, total={data.get('total_width_mm')}мм)"
                )
            else:
                # ── 1. Протокол стабильности: до 3 попыток + масштаб + голосование ──
                attempts: list = []
                
                if cached:
                    # Неподтверждённый кэш = попытка 1 (без вызова vision)
                    attempts.append(_attempt_from_cache(cached))
                    _log_attempt(1, attempts[0], " (из кэша)")
                else:
                    attempts.append(await vision_attempt())
                    vision_calls += 1
                    _log_attempt(1, attempts[0])
                
                a1 = attempts[0]
                
                # No-door гейт ДО протокола: пустые modules в no-door-зоне
                # (кровать/стол/диван) — legacy-fallback НЕ вызываем.
                if not a1["modules"] and is_no_door_zone_page(a1.get("zone_type"), a1["modules"]):
                    no_door_pages.append(path.name)
                    print(f"   ⏭️ Не корпусная мебель: {a1.get('zone_type')} — расчёт не требуется")
                    continue
                
                accepted = False
                consensus = False
                needs_operator = False
                chosen: dict = {}
                
                if a1["modules"] and a1["scale_ok"] and a1["confidence"] == "high":
                    # Попытка 1: масштаб сошёлся и уверенность высокая → принять
                    accepted = True
                    chosen = a1
                    print("   ✅ Попытка 1: масштаб сошёлся (scale_ok, high) — принимаем")
                else:
                    a2 = await vision_attempt()
                    vision_calls += 1
                    attempts.append(a2)
                    _log_attempt(2, a2)
                    
                    verdict = choose_attempt(attempts)
                    chosen, consensus, needs_operator = (
                        verdict["chosen"], verdict["consensus"], verdict["needs_operator"],
                    )
                    # ПРИНИМАЕМ ТОЛЬКО при сошедшемся масштабе (по ТЗ заказчика).
                    # Голосование бредовых чисел НЕ считается истиной.
                    if chosen.get("scale_ok"):
                        accepted = True
                    elif _attempt_better(a2, a1):
                        a3 = await vision_attempt()
                        vision_calls += 1
                        attempts.append(a3)
                        _log_attempt(3, a3)
                        
                        verdict = choose_attempt(attempts)
                        chosen, consensus, needs_operator = (
                            verdict["chosen"], verdict["consensus"], verdict["needs_operator"],
                        )
                        if chosen.get("scale_ok"):
                            accepted = True
                    # иначе: остаёмся на 2 попытках, протокол не принял
                
                n_attempts = len(attempts)
                
                if accepted:
                    if consensus:
                        print(f"   🤝 Голосование: {n_attempts} попыток сошлись — принимаем результат")
                    else:
                        print(f"   ✅ Масштаб сошёлся (scale_ok) — принимаем ({n_attempts} попыток)")
                    modules = chosen.get("modules") or []
                    zone_type = chosen.get("zone_type")
                    materials = chosen.get("materials") or []
                    confidence = chosen.get("confidence", "low")
                    method = f"протокол стабильности ({n_attempts} попыток)"
                    recognition_cache.save(
                        path, UNIFIED_PROMPT_V2, _data_for_cache(chosen),
                        approved=True, attempts=vision_calls,
                    )
                elif chosen.get("modules"):
                    # ── Fallback «нет размеров»: масштаб не сошёлся, размеров нет.
                    # Инвариант: в не-принятой ветке needs_operator всегда True
                    # (иначе была бы scale_ok-попытка/консенсус → accepted).
                    # Результат НЕ выбрасываем: считаем по средним общепринятым
                    # размерам, масштабируя на всю мебель (QC увидит confidence=low).
                    print(f"   ⚠️ Размеры не сошлись ({n_attempts} попыток, needs_operator={needs_operator})")
                    print("   📏 Стандартные размеры (масштаб не сошёлся)")
                    counts = door_counts_by_zone(chosen["modules"])
                    # Неправдоподобный габарит (например, 9360мм) НЕ используем
                    # для масштабирования — считаем по стандарту 600/дверь.
                    raw_total = chosen.get("total_width_mm") or 0
                    std_total = raw_total if plausible_total(raw_total) else 0
                    std = build_standard_modules(
                        chosen.get("zone_type"),
                        std_total,
                        counts or door_count_of(chosen["modules"]),
                    )
                    modules = recognition_cache.dicts_to_modules(std)
                    zone_type = chosen.get("zone_type")
                    materials = chosen.get("materials") or []
                    confidence = "low"  # QC это увидит
                    method = "стандартные размеры (масштаб не сошёлся)"
                    std_data = _data_for_cache(chosen, modules=modules)
                    std_data["confidence"] = "low"
                    std_data["scale_ok"] = False
                    recognition_cache.save(
                        path, UNIFIED_PROMPT_V2, std_data,
                        approved=False, attempts=vision_calls,
                    )
                else:
                    # Модулей нет после протокола → legacy-fallback (как раньше)
                    print(f"   ⚠️ Размеры не сошлись ({n_attempts} попыток, needs_operator={needs_operator})")
                    final_zone = chosen.get("zone_type") or (
                        attempts[-1].get("zone_type") if attempts else None
                    )
                    if is_no_door_zone_page(final_zone, []):
                        no_door_pages.append(path.name)
                        print(f"   ⏭️ Не корпусная мебель: {final_zone} — расчёт не требуется")
                        continue
                    print("   ⚠️ Единый анализ не дал модулей → fallback GLM-5V-Turbo...")
                    fallback_result = await analyzer.analyze_drawing(path)
                    modules = fallback_result.modules
                    zone_type = fallback_result.zone_type
                    materials = fallback_result.materials_mentioned
                    confidence = fallback_result.confidence
                    method = "fallback (GLM-5V)"
            
            if modules:
                # Определяем имя помещения
                zone_names = {
                    "kitchen": "Кухня", "living_room": "Гостиная",
                    "bedroom": "Спальня", "wardrobe": "Гардеробная",
                    "bathroom": "Ванная", "hallway": "Прихожая",
                    "kids_room": "Детская", "office": "Кабинет",
                    "laundry": "Постирочная", "balcony": "Балкон",
                    "dining_room": "Столовая",
                }
                zone_ru = zone_type or ""
                if zone_ru in zone_names:
                    zone_ru = zone_names[zone_ru]
                
                room_name = zone_ru or f"Помещение {i}"
                if len(image_paths) == 1:
                    room_name = zone_ru or path.stem
                
                room = RoomSpec(
                    room_name=room_name,
                    page=i,
                    zone_type=zone_type,
                    modules=modules,
                    materials=materials,
                    confidence=confidence,
                )
                _calculate_quality(room)  # QC-скор и флаги для листа «Контроль качества»
                rooms.append(room)
                
                print(f"   ✅ {room_name}: {len(modules)} модулей ({method})")
                for m in modules:
                    g = '🪟' if m.has_glass else ' '
                    c = '📐' if m.is_corner else ' '
                    print(f"      {g}{c} {m.type}: {m.width}×{m.depth}×{m.height}mm ×{m.quantity}")
                if materials:
                    print(f"   🎨 Материалы: {', '.join(materials[:10])}")
                print(f"   📊 Уверенность: {confidence}")
            else:
                errors.append(f"{path.name}: модули не распознаны")
                print(f"   ❌ Модули не найдены ни одной моделью")
        
        if not rooms:
            if no_door_pages and not errors:
                print(
                    "\n⏭️ Страница содержит не корпусную мебель — расчёт не требуется."
                )
                print("   Модули корпусной мебели отсутствуют, Excel-смета не формируется.")
                return ""
            print("\n❌ Ни одного помещения с модулями не распознано.")
            if errors:
                print("Ошибки:")
                for e in errors:
                    print(f"   - {e}")
            return ""
        
        # ── Шаг 2: Сборка PipelineResult ──
        result = PipelineResult(
            success=True,
            project_name=Path(image_paths[0]).stem if image_paths else "Проект",
            rooms=rooms,
            errors=errors,
        )
        
        total_modules = sum(len(r.modules) for r in rooms)
        print(f"\n{'=' * 60}")
        print(f"📊 ИТОГО: {len(rooms)} помещений, {total_modules} модулей")
        print(f"{'=' * 60}")
        
        # ── Шаг 3: Заполнение Excel ──
        output_path = OUTPUT_DIR / f"{output_name}.xlsx"
        print(f"\n📄 Заполнение шаблона → {output_path}...")
        
        fill_template_from_pipeline(
            pipeline_result=result,
            template_path=str(TEMPLATE),
            output_path=str(output_path),
        )
        
        size_kb = output_path.stat().st_size // 1024
        print(f"\n{'=' * 60}")
        print(f"✅ Excel сохранён: {output_path}")
        print(f"📏 Размер: {size_kb} KB")
        print(f"{'=' * 60}")
        
        return str(output_path)
    
    finally:
        await analyzer.close()


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n❌ Укажи пути к картинкам:")
        print("   python process_images.py кухня_стр1.jpg кухня_стр2.jpg")
        print("\nИли перетащи файлы на этот скрипт.")
        return
    
    image_paths = sys.argv[1:]
    output_name = "Расчет_по_картинкам"
    
    # Если передан один файл — называем по нему
    if len(image_paths) == 1:
        output_name = f"Расчет_{Path(image_paths[0]).stem}"
    
    await process_images(image_paths, output_name)


if __name__ == "__main__":
    asyncio.run(main())
