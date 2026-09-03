"""
Быстрая обработка: параллельно по 3 страницы → Excel.
Все страницы альбома, каждая через AI (Qwen3-VL + GLM-5V fallback).
"""
import asyncio, sys, logging, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-7s %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

from app.services.image_analyzer import GeminiImageAnalyzer
from app.services.full_pipeline import PipelineResult, RoomSpec
from app.services.template_filler import fill_template_from_pipeline

IMAGES_DIR = Path(r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\картинки")
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "Таблица для расчетов пустая.xlsx"
OUTPUT = Path(__file__).resolve().parent.parent / "output" / "Расчет_Рокоссовского_59-79.xlsx"

ZONE_NAMES = {
    "kitchen": "Кухня", "living_room": "Гостиная", "bedroom": "Спальня",
    "wardrobe": "Гардеробная", "bathroom": "Ванная", "hallway": "Прихожая",
    "kids_room": "Детская", "office": "Кабинет", "laundry": "Постирочная",
    "balcony": "Балкон", "dining_room": "Столовая",
}

async def analyze_one(analyzer: GeminiImageAnalyzer, img_path: Path, page_num: int):
    """Анализ одной страницы с fallback."""
    try:
        modules, zone_type, materials, confidence = await analyzer.analyze_page(img_path)
        method = "Qwen3-VL"
        if not modules:
            fb = await analyzer.analyze_drawing(img_path)
            modules, zone_type, materials, confidence = fb.modules, fb.zone_type or zone_type, fb.materials_mentioned or materials, fb.confidence or confidence
            method = "GLM-5V"
        return page_num, modules, zone_type, materials, confidence, method, None
    except Exception as e:
        return page_num, [], None, [], "low", "ERROR", str(e)

async def main():
    images = sorted(IMAGES_DIR.glob("*.jpg"))
    if not images:
        print(f"❌ Нет .jpg в {IMAGES_DIR}"); return
    
    print(f"📁 {len(images)} страниц | 🔄 параллельно ×3")
    print(f"📤 Результат: {OUTPUT}\n")
    
    analyzer = GeminiImageAnalyzer()
    t0 = time.monotonic()
    
    try:
        # Разбиваем на батчи по 3
        all_results = []
        total = len(images)
        
        for batch_start in range(0, total, 3):
            batch = images[batch_start:batch_start + 3]
            tasks = [analyze_one(analyzer, p, i + 1) for i, p in enumerate(batch, batch_start)]
            
            batch_results = await asyncio.gather(*tasks)
            all_results.extend(batch_results)
            
            # Прогресс
            done = min(batch_start + 3, total)
            elapsed = time.monotonic() - t0
            eta = (elapsed / done) * (total - done) if done > 0 else 0
            print(f"   ⏳ {done}/{total} страниц | прошло {elapsed:.0f}с | осталось ~{eta:.0f}с")
        
        # Сборка результата
        rooms = []
        total_modules = 0
        pages_with = 0
        
        for page_num, modules, zone_type, materials, confidence, method, error in sorted(all_results):
            if error:
                print(f"   ❌ Стр. {page_num}: {error}")
                continue
            
            if modules:
                pages_with += 1
                total_modules += len(modules)
                zone_ru = ZONE_NAMES.get((zone_type or "").lower().replace(" ", "_"), zone_type or "")
                room_name = zone_ru or f"Страница {page_num}"
                
                room = RoomSpec(
                    room_name=room_name, page=page_num, zone_type=zone_type,
                    modules=modules, materials=materials, confidence=confidence,
                )
                rooms.append(room)
                print(f"   ✅ Стр. {page_num}: {room_name} — {len(modules)} мод. ({method})")
            else:
                print(f"   ⏭️  Стр. {page_num}: без модулей")
        
        elapsed = time.monotonic() - t0
        print(f"\n{'='*60}")
        print(f"✅ Готово за {elapsed:.0f}с: {pages_with} помещений, {total_modules} модулей")
        print(f"{'='*60}")
        
        if not rooms:
            print("❌ Модули не найдены.")
            return
        
        # Excel
        result = PipelineResult(success=True, project_name="Рокоссовского 59-79", rooms=rooms)
        OUTPUT.parent.mkdir(exist_ok=True)
        fill_template_from_pipeline(result, str(TEMPLATE), str(OUTPUT))
        print(f"\n📊 Excel: {OUTPUT} ({OUTPUT.stat().st_size//1024} KB)")
        
    finally:
        await analyzer.close()

if __name__ == "__main__":
    asyncio.run(main())
