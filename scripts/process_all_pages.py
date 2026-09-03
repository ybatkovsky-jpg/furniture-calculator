"""
Обработка ВСЕХ страниц альбома → Excel-смета.
Каждая страница анализируется через AI (Qwen3-VL-235B + GLM-5V fallback).

Использование:
    python process_all_pages.py
"""

import asyncio
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-7s %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

from app.services.image_analyzer import GeminiImageAnalyzer
from app.services.full_pipeline import PipelineResult, RoomSpec
from app.services.template_filler import fill_template_from_pipeline

# Пути
IMAGES_DIR = Path(r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\картинки")
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "Таблица для расчетов пустая.xlsx"
OUTPUT = Path(__file__).resolve().parent.parent / "output" / "Расчет_Рокоссовского_59-79_ВСЕ.xlsx"

ZONE_NAMES = {
    "kitchen": "Кухня", "living_room": "Гостиная", "bedroom": "Спальня",
    "wardrobe": "Гардеробная", "bathroom": "Ванная", "hallway": "Прихожая",
    "kids_room": "Детская", "office": "Кабинет", "laundry": "Постирочная",
    "balcony": "Балкон", "dining_room": "Столовая",
}


async def process_all():
    # Собираем все JPEG страницы
    images = sorted(IMAGES_DIR.glob("*.jpg"))
    if not images:
        print(f"❌ Нет .jpg в {IMAGES_DIR}")
        return
    
    print(f"📁 Найдено {len(images)} страниц")
    print(f"📄 Шаблон: {TEMPLATE}")
    print(f"📤 Результат: {OUTPUT}")
    print()
    
    analyzer = GeminiImageAnalyzer()
    
    try:
        rooms = []
        errors = []
        pages_with_modules = 0
        pages_without_modules = 0
        total_modules = 0
        
        for i, img_path in enumerate(images, 1):
            page_num = i
            # Извлекаем номер страницы из имени файла
            fname = img_path.stem
            if "_page-" in fname:
                try:
                    page_num = int(fname.split("_page-")[-1])
                except ValueError:
                    pass
            
            size_kb = img_path.stat().st_size // 1024
            print(f"🖼️  Стр. {page_num}/{len(images)}: {img_path.name} ({size_kb} KB)")
            
            # ── Единый анализ (Qwen3-VL-235B) ──
            modules, zone_type, materials, confidence = await analyzer.analyze_page(img_path)
            
            if not modules:
                # ── Fallback: GLM-5V-Turbo ──
                fb = await analyzer.analyze_drawing(img_path)
                modules = fb.modules
                zone_type = zone_type or fb.zone_type
                materials = materials or fb.materials_mentioned
                confidence = fb.confidence or confidence
            
            if modules:
                pages_with_modules += 1
                total_modules += len(modules)
                zone_ru = ZONE_NAMES.get((zone_type or "").lower().replace(" ", "_"), zone_type or "")
                room_name = zone_ru or f"Страница {page_num}"
                
                room = RoomSpec(
                    room_name=room_name,
                    page=page_num,
                    zone_type=zone_type,
                    modules=modules,
                    materials=materials,
                    confidence=confidence,
                )
                rooms.append(room)
                
                print(f"   ✅ {room_name}: {len(modules)} модулей, confidence={confidence}")
                for m in modules:
                    g = '🪟' if m.has_glass else ' '
                    c = '📐' if m.is_corner else ' '
                    print(f"      {g}{c} {m.type}: {m.width}×{m.depth}×{m.height}mm ×{m.quantity}")
                if materials:
                    print(f"   🎨 {', '.join(materials[:8])}")
            else:
                pages_without_modules += 1
                print(f"   ⏭️  Модули не найдены (возможно, информационная страница)")
        
        # ── Итоги ──
        print()
        print("=" * 60)
        print(f"📊 ИТОГО:")
        print(f"   Страниц обработано: {len(images)}")
        print(f"   С модулями: {pages_with_modules}")
        print(f"   Без модулей: {pages_without_modules}")
        print(f"   Помещений: {len(rooms)}")
        print(f"   Модулей всего: {total_modules}")
        print("=" * 60)
        
        if not rooms:
            print("❌ Ни одного модуля не найдено. Проверь качество изображений.")
            return
        
        # ── Заполнение Excel ──
        result = PipelineResult(
            success=True,
            project_name="Рокоссовского 59-79",
            rooms=rooms,
            errors=errors,
        )
        
        OUTPUT.parent.mkdir(exist_ok=True)
        print(f"\n📊 Заполнение шаблона...")
        fill_template_from_pipeline(result, str(TEMPLATE), str(OUTPUT))
        
        print(f"\n✅ Готово: {OUTPUT}")
        print(f"📏 Размер: {OUTPUT.stat().st_size // 1024} KB")
        
    finally:
        await analyzer.close()


if __name__ == "__main__":
    asyncio.run(process_all())
