"""Генерация golden-набора по «волонтёрскому центру».

8 позиций КП: габариты и количество — из текста, состав — ручная разметка
по рендеру (зафиксирована здесь, подтверждается техдиром). Скрипт считает
эталонную цену движком и пишет `tests/fixtures/kp_volunteer_golden.json`.

Запуск: .\\.venv\\Scripts\\python.exe scripts/kp_golden_build.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.calc_engine import calculate_full_cost

OUT = Path("tests/fixtures/kp_volunteer_golden.json")

MATS = {
    "ldsp": {"name": "ТОМЛЕСДРЕВ белый", "price": 3600, "price_unit": "лист",
             "sheet_width_mm": 2800, "sheet_height_mm": 2070},
    "facades": {"name": "ЛДСП Томлесдрев", "price": 3600, "price_unit": "лист",
                "sheet_width_mm": 2800, "sheet_height_mm": 2070},
    "edge": {"name": "EGGER 0.8*19", "price": 41, "thickness_mm": 0.8},
}
HW = {"hinges": {"price": 200, "brand": "FIRMAX"},
      "drawers": {"price": 3300}, "handles": {"price": 500}}
PB = {"hdf": 1380, "confirmat": 2, "adjustable_feet": 50, "plinth": 1500,
      "edge_0.4": 22, "countertop": 2834}

# Состав — по рендеру КП (ручная разметка).
ITEMS = [
    {
        "title": "Кухонный гарнитур 3000×1700 H2550",
        "width": 3000, "depth": 1700, "height": 2550, "qty": 1,
        "kp_unit": 375_000,
        "zone": "Кухня",
        "modules": [
            {"type": "lower_base", "width": 600, "depth": 560, "height": 820,
             "quantity": 5, "facades": {"count": 1}, "shelves": 1},
            {"type": "upper_base", "width": 600, "depth": 300, "height": 720,
             "quantity": 5, "facades": {"count": 1}, "shelves": 1},
            {"type": "penal", "width": 600, "depth": 560, "height": 2100,
             "quantity": 2, "facades": {"count": 2}, "shelves": 2},
        ],
    },
    {
        "title": "Шкаф большой под одежду 2000×550 H2550",
        "width": 2000, "depth": 550, "height": 2550, "qty": 1,
        "kp_unit": 159_000,
        "zone": "Гардеробная",
        "modules": [
            {"type": "penal", "width": 2000, "depth": 550, "height": 2550,
             "quantity": 1, "facades": {"count": 4}, "shelves": 4},
        ],
    },
    {
        "title": "Ресепшн 1800×700 H1100",
        "width": 1800, "depth": 700, "height": 1100, "qty": 1,
        "kp_unit": 149_000,
        "zone": "Офис",
        "modules": [
            {"type": "lower_base", "width": 1800, "depth": 700, "height": 1100,
             "quantity": 1, "facades": {"count": 3}, "drawers": {"count": 3},
             "shelves": 1},
        ],
    },
    {
        "title": "Стол рабочий 1800×700 H1100",
        "width": 1800, "depth": 700, "height": 1100, "qty": 8,
        "kp_unit": 45_700,
        "zone": "Офис",
        "modules": [
            {"type": "open_unit", "width": 1800, "depth": 700, "height": 750,
             "quantity": 1, "shelves": 1},
            {"type": "drawer_unit", "width": 400, "depth": 700, "height": 750,
             "quantity": 1, "drawers": {"count": 3}},
        ],
    },
    {
        "title": "Шкаф для бумаг 1000×450 H2550",
        "width": 1000, "depth": 450, "height": 2550, "qty": 5,
        "kp_unit": 54_000,
        "zone": "Офис",
        "modules": [
            {"type": "penal", "width": 1000, "depth": 450, "height": 2550,
             "quantity": 1, "facades": {"count": 2}, "shelves": 5},
        ],
    },
    {
        "title": "Стеллаж-витрина 1000×450 H2550",
        "width": 1000, "depth": 450, "height": 2550, "qty": 2,
        "kp_unit": 109_000,
        "zone": "Офис",
        "modules": [
            {"type": "lower_base", "width": 1000, "depth": 450, "height": 820,
             "quantity": 1, "facades": {"count": 2}, "shelves": 1},
            {"type": "upper_base", "width": 1000, "depth": 450, "height": 1700,
             "quantity": 1, "facades": {"count": 2, "type": "glass"},
             "shelves": 3, "has_glass": True},
        ],
    },
    {
        "title": "Трибуна 600×550 H1200",
        "width": 600, "depth": 550, "height": 1200, "qty": 1,
        "kp_unit": 46_200,
        "zone": "Офис",
        "modules": [
            {"type": "open_unit", "width": 600, "depth": 550, "height": 1200,
             "quantity": 1, "shelves": 2},
        ],
    },
    {
        "title": "Стол мобильный 1000×600 H750",
        "width": 1000, "depth": 600, "height": 750, "qty": 6,
        "kp_unit": 20_300,
        "zone": "Офис",
        "modules": [
            {"type": "open_unit", "width": 1000, "depth": 600, "height": 750,
             "quantity": 1, "shelves": 1},
        ],
    },
]


def main() -> int:
    rows = []
    for item in ITEMS:
        result = calculate_full_cost(
            item["modules"], MATS, HW, [], zone_type=item["zone"], price_book=PB
        )
        row = {
            **item,
            "our_unit": round(result.cost_breakdown.furniture_cash),
            "our_total": round(result.cost_breakdown.furniture_cash) * item["qty"],
            "kp_total": item["kp_unit"] * item["qty"],
        }
        rows.append(row)
        delta = (row["our_total"] - row["kp_total"]) / row["kp_total"] * 100
        print(f"{item['title'][:44]:44} КП={row['kp_total']:>8} "
              f"наш={row['our_total']:>8} Δ={delta:+.1f}%")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"source": "KP/Коммерческое предложение волонтерский центр.pdf",
                    "annotation": "состав по рендеру, подтверждается техдиром",
                    "items": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nСохранено: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
