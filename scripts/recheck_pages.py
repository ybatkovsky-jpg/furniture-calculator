"""
Перепроверка страниц 2 и 3 — ищем второй пенал и проверяем остров.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.image_analyzer import GeminiImageAnalyzer
from app.services.pdf_renderer import render_page


async def main():
    pdf = Path(r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\Альбом чертежей Рокоссовского-59-79_compressed.pdf")

    analyzer = GeminiImageAnalyzer()

    for pg in [1, 2]:  # 0-based: pages 2 and 3
        print(f"\n{'='*70}")
        print(f"📄 СТРАНИЦА {pg+1}")
        print(f"{'='*70}")

        jpeg = render_page(pdf, pg)
        tmp = Path(f"temp/recheck_pg{pg+1}.jpg")
        tmp.parent.mkdir(exist_ok=True)
        tmp.write_bytes(jpeg)

        result = await analyzer.analyze_drawing(str(tmp))

        print(f"🏠 Zone type: {result.zone_type}")
        print(f"🎯 Confidence: {result.confidence}")
        print(f"🎨 Materials: {result.materials_mentioned}")
        print(f"\n📦 Modules ({len(result.modules)}):")

        for i, m in enumerate(result.modules, 1):
            glass = "🪟" if m.has_glass else "  "
            corner = "📐" if m.is_corner else "  "
            qty = m.quantity or 1
            print(f"  {i:2d}. {glass}{corner} {m.type:15s} "
                  f"{m.width:>5d}×{m.depth:>4d}×{m.height:>5d}mm  "
                  f"×{qty}  (total width: {m.width * qty}mm)")

        # Суммарная ширина по типам
        print(f"\n📏 Суммарная ширина по типам:")
        from collections import defaultdict
        totals = defaultdict(int)
        for m in result.modules:
            totals[m.type] += m.width * (m.quantity or 1)
        for t, w in sorted(totals.items()):
            print(f"  {t}: {w}mm")

        # Ищем пеналы отдельно
        penals = [m for m in result.modules if m.type == "penal"]
        print(f"\n🔍 Пеналов найдено: {len(penals)}")
        for p in penals:
            print(f"  {p.width}×{p.depth}×{p.height}mm ×{p.quantity or 1}")

        # Raw JSON if available
        if hasattr(result, 'raw_response') and result.raw_response:
            print(f"\n📝 Raw AI response (первые 500 символов):")
            print(result.raw_response[:500])

    await analyzer.close()


if __name__ == "__main__":
    asyncio.run(main())
