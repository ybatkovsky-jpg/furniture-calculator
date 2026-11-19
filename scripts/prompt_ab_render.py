"""A/B промпта на страницах КП: базовый vs «рендер-режим».

Гипотеза: на 3D-рендере без размерных линий базовый промпт не требует
перечислять все корпуса, поэтому VLM возвращает 1–3 группы вместо 10–15.
Добавка в промпт должна увеличить число групп и приблизить цену к КП.

Запуск: .\\.venv\\Scripts\\python.exe scripts/prompt_ab_render.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services import image_analyzer
from app.services.calc_engine import calculate_full_cost

OUT = Path("docs/prompt_ab_render_2026-11-17.json")
TMP = Path("temp/prompt_ab")
TMP.mkdir(parents=True, exist_ok=True)

ADDENDUM = """

═══════════════════════════════════════
РЕЖИМ 3D-РЕНДЕРА (если размерных линий нет)
═══════════════════════════════════════
Если на изображении 3D-визуализация, а не чертёж с размерами:
1. Перечисли ВСЕ корпуса, которые видишь: нижние, верхние, пеналы, остров,
   отдельно стоящие тумбы. Не объединяй разные корпуса в одну запись.
2. Одинаковые корпуса рядом — одна запись с quantity = их число.
3. Ширину оцени по пропорциям; по умолчанию 600 мм (низ/верх), 560/300 мм
   глубина, 820/720/2100 мм высота.
4. Если видишь 8 и больше корпусов — верни 8 и больше записей.
5. total_width_mm = суммарная ширина нижнего ряда.
"""

PB = {"hdf": 1380, "confirmat": 2, "adjustable_feet": 50, "plinth": 1500, "edge_0.4": 22}
MATS = {
    "ldsp": {"name": "ЛДСП", "price": 6000, "price_unit": "лист"},
    "facades": {"name": "Фасад", "price": 10200, "price_unit": "лист",
                "sheet_width_mm": 2800, "sheet_height_mm": 1220},
    "edge": {"name": "EGGER 0.8*19", "price": 41, "thickness_mm": 0.8},
}
HW = {"hinges": {"price": 200, "brand": "FIRMAX"},
      "drawers": {"price": 3300}, "handles": {"price": 500}}

PAGES = [
    ("KP/КП ЖК Гаромония.pdf", 1, 978500),
    ("KP/КП Кухня и ТВ зона с витринами и обеденной зоной от 29.07.2026.pdf", 2, 1092000),
    ("KP/КП Виктор и Юлия ЖК Дежнев.pdf", 5, 729700),
    ("KP/КП ЖК МАРШАЛ от 29.05.2026.pdf", 3, 188000),
]


def _price(modules) -> int:
    if not modules:
        return 0
    result = calculate_full_cost(modules, MATS, HW, [], zone_type="Кухня", price_book=PB)
    return round(result.cost_breakdown.furniture_cash)


def _dicts(recognition):
    return [
        {"type": m.type, "width": m.width, "depth": m.depth, "height": m.height,
         "quantity": m.quantity, "facades": m.facades, "drawers": m.drawers,
         "shelves": m.shelves, "has_glass": m.has_glass}
        for m in recognition.modules
    ]


async def main() -> int:
    base_prompt = image_analyzer.UNIFIED_PROMPT_V2
    analyzer = image_analyzer.get_analyzer()
    rows = []
    for path, page_no, kp in PAGES:
        doc = pymupdf.open(path)
        image = TMP / f"{Path(path).stem[:30]}_{page_no}.png"
        doc[page_no - 1].get_pixmap(dpi=110).save(image)

        image_analyzer.UNIFIED_PROMPT_V2 = base_prompt
        base = _dicts(await analyzer.analyze_drawing(str(image)))

        image_analyzer.UNIFIED_PROMPT_V2 = base_prompt + ADDENDUM
        render = _dicts(await analyzer.analyze_drawing(str(image)))

        rows.append({
            "file": Path(path).name, "page": page_no, "kp": kp,
            "base_modules": len(base), "render_modules": len(render),
            "base_price": _price(base), "render_price": _price(render),
            "base_delta": round((_price(base) - kp) / kp * 100, 1),
            "render_delta": round((_price(render) - kp) / kp * 100, 1),
        })
        print(f"{Path(path).name[:40]:40} p{page_no} "
              f"модулей {len(base)}→{len(render)} | "
              f"Δ {rows[-1]['base_delta']}%→{rows[-1]['render_delta']}%")

    image_analyzer.UNIFIED_PROMPT_V2 = base_prompt
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСохранено: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
