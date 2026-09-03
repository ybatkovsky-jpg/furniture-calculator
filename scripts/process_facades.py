"""
ФАСАДНЫЙ ПОДХОД: перечисляем КАЖДУЮ дверцу отдельно → группируем в модули в Python.
Без путаницы «фасад vs модуль» — модель только считает двери.

Использование:
    python process_facades.py
"""

import asyncio, sys, logging, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-7s %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

from app.services.image_analyzer import GeminiImageAnalyzer, RecognizedModule, FacadeData
from app.services.full_pipeline import PipelineResult, RoomSpec
from app.services.template_filler import fill_template_from_pipeline

IMAGES_DIR = Path(r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\картинки")
TEMPLATE = Path(__file__).parent / "templates" / "Таблица для расчетов пустая.xlsx"
OUTPUT = Path(__file__).parent / "output" / "Расчет_Рокоссовского_59-79_ФАСАДЫ.xlsx"

ZONE_NAMES = {
    "Кухня": "Кухня", "Гостиная": "Гостиная", "Спальня": "Спальня",
    "Гардеробная": "Гардеробная", "Ванная": "Ванная", "Прихожая": "Прихожая",
    "Детская": "Детская", "Кабинет": "Кабинет", "Постирочная": "Постирочная",
    "Балкон": "Балкон", "Столовая": "Столовая",
}


def facades_to_modules(facades: list[FacadeData]) -> list[RecognizedModule]:
    """
    Сгруппировать отдельные фасады в модули мебели.
    
    Правила:
    - Фасады одной зоны (lower/upper/penal) рядом → ОДИН модуль
    - Угловой фасад → отдельный модуль corner
    - Пенал → всегда отдельный модуль
    - Два фасада рядом вплотную (gap < 50mm) → ОДИН модуль с facades.count=2
    - Одиночный фасад → модуль с facades.count=1
    """
    if not facades:
        return []
    
    modules = []
    
    # Сортируем по зоне и x-позиции (оценочно по порядку)
    zone_order = {"lower": 0, "upper": 1, "penal": 2, "corner": 3}
    
    # Группируем по зонам
    by_zone = {}
    for f in facades:
        zone = f.zone if f.zone in ("lower", "upper", "penal") else "lower"
        by_zone.setdefault(zone, []).append(f)
    
    for zone in ["lower", "upper", "penal"]:
        zone_facades = by_zone.get(zone, [])
        if not zone_facades:
            continue
        
        # Для каждой зоны группируем соседние фасады
        i = 0
        while i < len(zone_facades):
            f = zone_facades[i]
            
            # Угловой → отдельный модуль
            if f.is_corner:
                modules.append(RecognizedModule(
                    type="corner",
                    width=f.width_mm,
                    depth=f.width_mm,  # corner: квадратный
                    height=f.height_mm,
                    quantity=1,
                    is_corner=True,
                    facades={"count": 1, "type": "doors"},
                ))
                i += 1
                continue
            
            # Пенал → всегда отдельный модуль
            if zone == "penal":
                modules.append(RecognizedModule(
                    type="penal",
                    width=f.width_mm,
                    depth=560,
                    height=f.height_mm,
                    quantity=1,
                    facades={"count": 1, "type": "doors"},
                ))
                i += 1
                continue
            
            # Считаем сколько фасадов в этом модуле (одинаковая ширина ± 20mm)
            module_facades = [f]
            j = i + 1
            while j < len(zone_facades):
                next_f = zone_facades[j]
                # Если следующий фасад близкой ширины (±20mm) — это тот же тип модуля
                if abs(next_f.width_mm - f.width_mm) <= 20 and not next_f.is_corner:
                    module_facades.append(next_f)
                    j += 1
                else:
                    break
            
            # Создаём модуль
            avg_width = sum(mf.width_mm for mf in module_facades) // len(module_facades)
            avg_height = sum(mf.height_mm for mf in module_facades) // len(module_facades)
            
            mtype = "upper_base" if zone == "upper" else "lower_base"
            depth = 320 if zone == "upper" else 560
            
            modules.append(RecognizedModule(
                type=mtype,
                width=avg_width,
                depth=depth,
                height=avg_height,
                quantity=len(module_facades),
                facades={"count": 1, "type": "doors"},
            ))
            
            i = j
    
    return modules


async def process_page(analyzer: GeminiImageAnalyzer, img_path: Path, page_num: int):
    """Обработать одну страницу: фасады → модули."""
    try:
        # Шаг 1: Фасадный анализ (перечисляем КАЖДУЮ дверцу)
        facade_result = await analyzer.analyze_facades(img_path)
        
        if not facade_result.facades:
            # Fallback: пробуем analyze_drawing
            fb = await analyzer.analyze_drawing(img_path)
            if fb.modules:
                return page_num, fb.modules, fb.zone_type, fb.materials_mentioned, fb.confidence, "fallback"
            return page_num, [], None, [], "low", "empty"
        
        # Шаг 2: Группируем фасады в модули (Python, не модель!)
        modules = facades_to_modules(facade_result.facades)
        
        zone_type = facade_result.zone_type
        materials = facade_result.materials_mentioned
        confidence = facade_result.confidence
        
        return page_num, modules, zone_type, materials, confidence, "facades"
        
    except Exception as e:
        logger.error(f"Стр. {page_num}: {type(e).__name__}: {e}")
        return page_num, [], None, [], "low", str(e)


async def main():
    images = sorted(IMAGES_DIR.glob("*.jpg"))
    if not images:
        print(f"❌ Нет .jpg в {IMAGES_DIR}"); return
    
    print(f"📁 {len(images)} страниц | 🚪 Фасадный подход (двери → модули)")
    print(f"📤 Результат: {OUTPUT}\n")
    
    analyzer = GeminiImageAnalyzer()
    t0 = time.monotonic()
    
    try:
        all_results = []
        total = len(images)
        
        # Параллельно по 3
        for batch_start in range(0, total, 3):
            batch = images[batch_start:batch_start + 3]
            tasks = [process_page(analyzer, p, batch_start + i + 1) for i, p in enumerate(batch)]
            batch_results = await asyncio.gather(*tasks)
            all_results.extend(batch_results)
            
            done = min(batch_start + 3, total)
            elapsed = time.monotonic() - t0
            eta = (elapsed / done) * (total - done) if done > 0 else 0
            print(f"   ⏳ {done}/{total} стр. | {elapsed:.0f}с | осталось ~{eta:.0f}с")
        
        # Сборка
        rooms = []
        total_mods = 0
        
        for page_num, modules, zone_type, materials, confidence, method in sorted(all_results):
            if modules:
                zone_ru = zone_type if zone_type in ZONE_NAMES else (zone_type or f"Стр.{page_num}")
                room = RoomSpec(
                    room_name=zone_ru, page=page_num, zone_type=zone_type,
                    modules=modules, materials=materials, confidence=confidence,
                )
                rooms.append(room)
                total_mods += len(modules)
                
                print(f"   ✅ Стр. {page_num}: {zone_ru} — {len(modules)} модулей ({method})")
                for m in modules:
                    g = '🪟' if m.has_glass else ' '
                    c = '📐' if m.is_corner else ' '
                    print(f"      {g}{c} {m.type}: {m.width}×{m.depth}×{m.height}mm ×{m.quantity}")
            else:
                print(f"   ⏭️  Стр. {page_num}: без модулей ({method})")
        
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
