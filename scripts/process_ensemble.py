"""
АНСАМБЛЬ: Qwen3-VL (FACADE_PROMPT) + GLM-5V (UNIFIED_PROMPT) = объединение фасадов.
Две модели → пересечение фасадов → группировка в модули → Excel.

Запуск: python process_ensemble.py
"""

import asyncio, sys, logging, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-7s %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

from app.services.image_analyzer import GeminiImageAnalyzer, RecognizedModule, FacadeData
from app.services.full_pipeline import PipelineResult, RoomSpec
from app.services.template_filler import fill_template_from_pipeline

IMAGES_DIR = Path(r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\картинки")
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "Таблица для расчетов пустая.xlsx"
OUTPUT = Path(__file__).resolve().parent.parent / "output" / "Расчет_Рокоссовского_59-79_АНСАМБЛЬ.xlsx"


def facades_to_modules(facades: list[FacadeData]) -> list[RecognizedModule]:
    """Сгруппировать фасады в модули."""
    if not facades:
        return []
    
    modules = []
    by_zone = {}
    for f in facades:
        zone = f.zone if f.zone in ("lower", "upper", "penal") else "lower"
        by_zone.setdefault(zone, []).append(f)
    
    for zone in ["lower", "upper", "penal"]:
        zone_facades = sorted(by_zone.get(zone, []), key=lambda f: f.width_mm)
        if not zone_facades:
            continue
        
        # Группируем одинаковые по ширине (±25mm)
        i = 0
        while i < len(zone_facades):
            f = zone_facades[i]
            
            if f.is_corner:
                modules.append(RecognizedModule(
                    type="corner", width=f.width_mm, depth=f.width_mm,
                    height=f.height_mm, quantity=1, is_corner=True,
                    facades={"count": 1, "type": "doors"},
                ))
                i += 1
                continue
            
            # Собираем группу фасадов с одинаковой шириной
            group = [f]
            j = i + 1
            while j < len(zone_facades):
                if abs(zone_facades[j].width_mm - f.width_mm) <= 30:
                    group.append(zone_facades[j])
                    j += 1
                else:
                    break
            
            avg_w = sum(g.width_mm for g in group) // len(group)
            avg_h = sum(g.height_mm for g in group) // len(group)
            
            mtype = {
                "lower": "lower_base", "upper": "upper_base", "penal": "penal"
            }.get(zone, "lower_base")
            
            depth = {"lower": 560, "upper": 320, "penal": 560}.get(zone, 560)
            
            modules.append(RecognizedModule(
                type=mtype, width=avg_w, depth=depth, height=avg_h,
                quantity=len(group),
                facades={"count": 1, "type": "doors"},
            ))
            
            i = j
    
    return modules


def merge_facades(qwen_facades: list[FacadeData], glm_modules: list[RecognizedModule]) -> list[FacadeData]:
    """
    Объединить фасады от Qwen (FACADE_PROMPT) и GLM (UNIFIED_PROMPT).
    
    Стратегия:
    1. Фасады от Qwen — основа (FACADE_PROMPT точнее считает двери)
    2. Если GLM нашёл модули, которых нет у Qwen — добавляем их как фасады
    3. Удаляем дубликаты (одинаковая зона + размер ±30mm)
    """
    result = list(qwen_facades)
    
    # Конвертируем GLM-модули в фасады
    for m in glm_modules:
        zone = "penal" if m.type == "penal" else (
            "upper" if m.type == "upper_base" else "lower"
        )
        if m.type == "corner":
            zone = "lower"
        
        # Каждый quantity — это отдельный фасад
        for _ in range(m.quantity):
            fd = FacadeData(
                width_mm=m.width, height_mm=m.height, zone=zone,
                is_corner=m.is_corner,
            )
            
            # Проверяем, нет ли уже такого фасада
            is_duplicate = False
            for existing in result:
                if (existing.zone == fd.zone 
                    and abs(existing.width_mm - fd.width_mm) <= 40
                    and abs(existing.height_mm - fd.height_mm) <= 50):
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                result.append(fd)
    
    return result


async def process_page(analyzer: GeminiImageAnalyzer, img_path: Path, page_num: int):
    """Обработать страницу: Qwen фасады + GLM модули → объединение."""
    try:
        # 1. Qwen: FACADE_PROMPT (все двери)
        qwen_result = await analyzer.analyze_facades(img_path)
        qwen_facades = qwen_result.facades
        
        # 2. GLM: UNIFIED_PROMPT (модули)
        glm_result = await analyzer.analyze_drawing(img_path)
        glm_modules = glm_result.modules
        
        # 3. Объединяем
        all_facades = merge_facades(qwen_facades, glm_modules)
        
        # 4. Группируем в модули
        modules = facades_to_modules(all_facades)
        
        zone_type = qwen_result.zone_type or glm_result.zone_type
        materials = qwen_result.materials_mentioned or glm_result.materials_mentioned
        confidence = qwen_result.confidence
        
        logger.info(
            f"Стр.{page_num}: Qwen={len(qwen_facades)}f, GLM={len(glm_modules)}m → "
            f"merged={len(all_facades)}f → {len(modules)} modules"
        )
        
        return page_num, modules, zone_type, materials, confidence, all_facades, None
        
    except Exception as e:
        logger.error(f"Стр.{page_num}: {type(e).__name__}: {e}")
        return page_num, [], None, [], "low", [], str(e)


async def main():
    images = sorted(IMAGES_DIR.glob("*.jpg"))
    if not images:
        print(f"❌ Нет .jpg в {IMAGES_DIR}"); return
    
    print(f"📁 {len(images)} страниц | 🤝 Ансамбль Qwen+GLM")
    print(f"📤 Результат: {OUTPUT}\n")
    
    analyzer = GeminiImageAnalyzer()
    t0 = time.monotonic()
    
    try:
        all_results = []
        total = len(images)
        
        # По 2 параллельно (2 модели на страницу = дольше)
        for batch_start in range(0, total, 2):
            batch = images[batch_start:batch_start + 2]
            tasks = [process_page(analyzer, p, batch_start + i + 1) for i, p in enumerate(batch)]
            batch_results = await asyncio.gather(*tasks)
            all_results.extend(batch_results)
            
            done = min(batch_start + 2, total)
            elapsed = time.monotonic() - t0
            eta = (elapsed / done) * (total - done) if done > 0 else 0
            print(f"   ⏳ {done}/{total} стр. | {elapsed:.0f}с | осталось ~{eta:.0f}с")
        
        # Сборка
        rooms = []
        total_mods = 0
        
        for page_num, modules, zone_type, materials, confidence, facades, error in sorted(all_results):
            if error:
                print(f"   ❌ Стр. {page_num}: {error}")
                continue
            
            if modules:
                zone_ru = zone_type or f"Стр.{page_num}"
                if zone_ru.lower() in ("kitchen",): zone_ru = "Кухня"
                elif zone_ru.lower() in ("living_room",): zone_ru = "Гостиная"
                
                room = RoomSpec(
                    room_name=zone_ru, page=page_num, zone_type=zone_type,
                    modules=modules, materials=materials, confidence=confidence,
                )
                rooms.append(room)
                total_mods += len(modules)
                
                print(f"   ✅ Стр.{page_num}: {zone_ru} — {len(modules)} модулей ({len(facades)} фасадов)")
                for m in modules:
                    c = '📐' if m.is_corner else '  '
                    print(f"      {c} {m.type}: {m.width}×{m.depth}×{m.height}mm ×{m.quantity}")
            else:
                print(f"   ⏭️  Стр.{page_num}: без модулей")
        
        elapsed = time.monotonic() - t0
        print(f"\n{'='*60}")
        print(f"✅ {len(rooms)} помещений, {total_mods} модулей за {elapsed:.0f}с")
        print(f"{'='*60}")
        
        if not rooms:
            print("❌ Модули не найдены.")
            return
        
        result = PipelineResult(success=True, project_name="Рокоссовского 59-79", rooms=rooms)
        OUTPUT.parent.mkdir(exist_ok=True)
        fill_template_from_pipeline(result, str(TEMPLATE), str(OUTPUT))
        print(f"\n📊 Excel: {OUTPUT} ({OUTPUT.stat().st_size//1024} KB)")
        
    finally:
        await analyzer.close()


if __name__ == "__main__":
    asyncio.run(main())
