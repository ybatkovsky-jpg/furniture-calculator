"""
Обработка отдельных изображений (не PDF) через AI-конвейер → Excel-смета.

Использование:
    python process_images.py image1.jpg image2.png ...

На каждую картинку:
1. Локальная Qwen3.8 (или RouterAI, если LOCAL_LLM_API_URL не задан) —
   единый анализ: bbox + модули + материалы (UNIFIED_PROMPT_V2)
2. analyze_drawing — fallback если 0 модулей
3. Заполнение шаблона Excel «Таблица для расчетов пустая.xlsx»
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

from app.services.image_analyzer import GeminiImageAnalyzer, RecognitionResult
from app.services.full_pipeline import PipelineResult, RoomSpec
from app.services.template_filler import fill_template_from_pipeline

TEMPLATE = ROOT / "templates" / "Таблица для расчетов пустая.xlsx"
OUTPUT_DIR = ROOT / "output"


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
        
        for i, img_path in enumerate(image_paths, 1):
            path = Path(img_path)
            if not path.exists():
                errors.append(f"Файл не найден: {img_path}")
                print(f"❌ [{i}/{len(image_paths)}] {path.name} — файл не найден")
                continue
            
            print(f"\n🖼️  [{i}/{len(image_paths)}] Анализ: {path.name} ({path.stat().st_size // 1024} KB)")
            print("   " + "─" * 60)
            
            # ── 1a. Единый анализ (Qwen3-VL-235B) ──
            modules, zone_type, materials, confidence = await analyzer.analyze_page(path)
            
            if modules:
                method = "единый (Qwen3-VL)"
            else:
                # ── 1b. Fallback: GLM-5V-Turbo ──
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
