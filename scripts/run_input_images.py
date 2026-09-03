"""
Обработка картинок из input_images через SCALE_PROMPT (bbox + ящики) → Excel.
"""
import asyncio, sys, logging, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-7s %(message)s', datefmt='%H:%M:%S')
logging.getLogger('httpx').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

from app.services.image_analyzer import GeminiImageAnalyzer
from app.services.full_pipeline import PipelineResult, RoomSpec
from app.services.template_filler import fill_template_from_pipeline
from app.services.project_spec import load_project_spec

IMAGES_DIR = Path(r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\furniture-calculator\input_images")
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "Таблица для расчетов пустая.xlsx"
OUTPUT = Path(__file__).resolve().parent.parent / "output" / "Расчет_Рокоссовского_ПОЛНЫЙ.xlsx"
SPEC = Path(r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\project_spec.yaml")


async def main():
    images = sorted(IMAGES_DIR.glob("*.jpg"))
    if not images:
        print(f"❌ Нет .jpg в {IMAGES_DIR}"); return
    
    print(f"📁 {len(images)} страниц | 📐 SCALE_PROMPT + ящики")
    print(f"📤 Результат: {OUTPUT}\n")
    
    analyzer = GeminiImageAnalyzer()
    t0 = time.monotonic()
    rooms = []
    
    try:
        for i, img_path in enumerate(images):
            page_num = i + 1
            size_kb = img_path.stat().st_size // 1024
            print(f"🖼️  Стр. {page_num}/{len(images)}: {img_path.name} ({size_kb} KB)")
            
            try:
                modules, zone_type, materials, confidence = await analyzer.analyze_page(img_path)
                method = "SCALE"
            except Exception as e:
                logger.warning(f"SCALE упал: {e}, fallback GLM-5V...")
                fb = await analyzer.analyze_drawing(img_path)
                modules = fb.modules
                zone_type = fb.zone_type
                materials = fb.materials_mentioned
                confidence = fb.confidence
                method = "GLM-5V"
            
            if modules:
                zone_ru = zone_type or f"Страница {page_num}"
                room = RoomSpec(
                    room_name=zone_ru, page=page_num, zone_type=zone_type,
                    modules=modules, materials=materials, confidence=confidence,
                )
                rooms.append(room)
                
                total_qty = sum(m.quantity for m in modules)
                drawers_count = sum(1 for m in modules if m.drawers)
                print(f"   ✅ {len(modules)} модулей ({total_qty} шт), {drawers_count} с ящиками ({method})")
                for m in modules:
                    c = '📐' if m.is_corner else '  '
                    g = '🪟' if m.has_glass else '  '
                    d = '🗄️' if m.drawers else '  '
                    print(f"      {c}{g}{d} {m.type}: {m.width}×{m.depth}×{m.height}mm ×{m.quantity}")
            else:
                print(f"   ⏭️  Без модулей")
            
            elapsed = time.monotonic() - t0
            eta = (elapsed / (i + 1)) * (len(images) - i - 1) if i > 0 else 0
            print(f"   ⏱️  {elapsed:.0f}с | осталось ~{eta:.0f}с\n")
        
        elapsed = time.monotonic() - t0
        total_mods = sum(sum(m.quantity for m in r.modules) for r in rooms)
        print(f"{'='*60}")
        print(f"✅ {len(rooms)} помещений, {total_mods} модулей за {elapsed:.0f}с")
        print(f"{'='*60}")
        
        if not rooms:
            print("❌ Модули не найдены.")
            return
        
        result = PipelineResult(success=True, project_name="Рокоссовского 59-79", rooms=rooms)
        spec = load_project_spec(str(SPEC)) if SPEC.exists() else None
        OUTPUT.parent.mkdir(exist_ok=True)
        fill_template_from_pipeline(result, str(TEMPLATE), str(OUTPUT), project_spec=spec)
        print(f"\n📊 Excel: {OUTPUT} ({OUTPUT.stat().st_size//1024} KB)")
        
    finally:
        await analyzer.close()


if __name__ == "__main__":
    asyncio.run(main())
