"""
Быстрый прогон: 2 страницы, без ансамбля для скорости.
"""
import asyncio, sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-7s %(message)s', datefmt='%H:%M:%S')

from app.services.full_pipeline import FullPipeline, print_result
from app.services.template_filler import fill_template_from_pipeline
from app.services.image_analyzer import GeminiImageAnalyzer

PDF = r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\Альбом чертежей Рокоссовского-59-79_compressed.pdf"
TEMPLATE = r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\furniture-calculator\templates\Таблица для расчетов пустая.xlsx"
OUTPUT = r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\furniture-calculator\output\Расчет_Рокоссовского_59-79.xlsx"

async def main():
    Path(OUTPUT).parent.mkdir(exist_ok=True)

    pipeline = FullPipeline()
    try:
        print("🚀 Конвейер (5 страниц, параллельно ×3)...")
        result = await pipeline.process(PDF, max_pages=5)
        print_result(result)

        if not result.success:
            print("❌ Нет помещений.")
            return

        print("\n📊 Генерация Excel...")
        output_path = fill_template_from_pipeline(result, TEMPLATE, OUTPUT)
        size_kb = Path(output_path).stat().st_size // 1024
        print(f"\n✅ Excel сохранён: {output_path} ({size_kb} KB)")

        # Покажем листы
        from openpyxl import load_workbook
        wb = load_workbook(output_path)
        print(f"📋 Листы: {wb.sheetnames}")

    finally:
        await pipeline.close()

if __name__ == "__main__":
    asyncio.run(main())
