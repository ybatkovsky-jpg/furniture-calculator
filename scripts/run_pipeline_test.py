"""
Полный тестовый прогон: PDF → распознавание → расчёт → Excel.
"""
import asyncio, sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-7s %(message)s',
    datefmt='%H:%M:%S'
)

from app.services.full_pipeline import FullPipeline, print_result
from app.services.template_filler import fill_template_from_pipeline
from app.services.project_spec import load_project_spec

PDF = r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\Альбом чертежей Рокоссовского-59-79_compressed.pdf"
TEMPLATE = r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\furniture-calculator\templates\Таблица для расчетов пустая.xlsx"
OUTPUT = r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\furniture-calculator\output\Расчет_Рокоссовского_59-79.xlsx"
SPEC = r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\project_spec.yaml"

async def main():
    Path(OUTPUT).parent.mkdir(exist_ok=True)

    # ── Шаг 1: Конвейер распознавания ──
    pipeline = FullPipeline()
    try:
        print("🚀 Шаг 1/2: Распознавание чертежей...")
        result = await pipeline.process(PDF, max_pages=5)
        print_result(result)

        if not result.success:
            print("❌ Конвейер не смог распознать ни одного помещения.")
            return

        # ── Шаг 2: Генерация Excel ──
        print()
        print("📊 Шаг 2/2: Заполнение Excel...")

        # Загружаем спецификацию проекта если есть
        spec = None
        if Path(SPEC).exists():
            spec = load_project_spec(SPEC)
            print(f"   📋 Спецификация проекта загружена: {SPEC}")

        output_path = fill_template_from_pipeline(
            pipeline_result=result,
            template_path=TEMPLATE,
            output_path=OUTPUT,
            project_spec=spec,
        )

        print()
        print("=" * 70)
        print(f"✅ Excel сохранён: {output_path}")
        print(f"📏 Размер: {Path(output_path).stat().st_size // 1024} KB")
        print("=" * 70)

    finally:
        await pipeline.close()

if __name__ == "__main__":
    asyncio.run(main())
