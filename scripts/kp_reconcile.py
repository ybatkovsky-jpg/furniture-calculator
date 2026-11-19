"""Сверка КП с расчётом конвейера.

Что делает:
1. По всем КП показывает, можно ли их пересчитать (есть ли размеры/состав).
2. Для «волонтёрского центра» — единственного КП с размерами и количеством —
   считает 8 позиций нашим конвейером и сравнивает с ценой КП.

Ограничение: в 21 КП из 22 нет ни размеров, ни состава модулей (только фото,
материалы и цена), поэтому точная сверка по ним невозможна.

Запуск: .\\.venv\\Scripts\\python.exe scripts/kp_reconcile.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.calc_engine import calculate_full_cost

SRC = Path("docs/kp_examples_2026-11-17.json")
OUT_MD = Path("docs/kp_reconciliation_2026-11-17.md")

MATS = {
    "ldsp": {"name": "ТОМЛЕСДРЕВ белый", "price": 3600, "price_unit": "лист"},
    "facades": {"name": "ЛДСП Томлесдрев", "price": 3600, "price_unit": "лист"},
    "edge": {"name": "EGGER 0.8*19", "price": 41, "thickness_mm": 0.8},
}
HW = {
    "hinges": {"price": 200, "brand": "FIRMAX"},
    "drawers": {"price": 3300},
    "handles": {"price": 500},
}
PB = {"hdf": 1380, "confirmat": 2, "adjustable_feet": 50, "plinth": 1500, "edge_0.4": 22}

# 8 позиций «волонтёрского центра»: размеры и количество — из КП,
# состав (двери/ящики/полки) — допущение, в КП его нет.
CASES = [
    ("Кухня 3000×1700 H2550", 375_000, "Кухня", [
        {"type": "lower_base", "width": 600, "depth": 560, "height": 820, "quantity": 4,
         "facades": {"count": 1}, "shelves": 1},
        {"type": "penal", "width": 600, "depth": 560, "height": 2100, "quantity": 1,
         "facades": {"count": 2}, "shelves": 2},
        {"type": "upper_base", "width": 600, "depth": 300, "height": 720, "quantity": 5,
         "facades": {"count": 1}, "shelves": 1},
    ]),
    ("Шкаф 2000×550 H2550", 159_000, "Гардеробная", [
        {"type": "penal", "width": 2000, "depth": 550, "height": 2550, "quantity": 1,
         "facades": {"count": 4}, "shelves": 5},
    ]),
    ("Ресепшн 1800×700 H1100", 149_000, "Офис", [
        {"type": "tumbler", "width": 1800, "depth": 700, "height": 1100, "quantity": 1,
         "facades": {"count": 3}, "drawers": {"count": 3}, "shelves": 1},
    ]),
    ("Стол 1800×700 H1100", 45_700, "Офис", [
        {"type": "tumbler", "width": 1800, "depth": 700, "height": 1100, "quantity": 1,
         "facades": {"count": 3}, "shelves": 1},
    ]),
    ("Шкаф для бумаг 1000×450 H2550", 54_000, "Офис", [
        {"type": "penal", "width": 1000, "depth": 450, "height": 2550, "quantity": 1,
         "facades": {"count": 2}, "shelves": 5},
    ]),
    ("Стеллаж-витрина 1000×450 H2550", 109_000, "Офис", [
        {"type": "penal", "width": 1000, "depth": 450, "height": 2550, "quantity": 1,
         "facades": {"count": 2, "type": "glass"}, "shelves": 5, "has_glass": True},
    ]),
    ("Трибуна 600×550 H1200", 46_200, "Офис", [
        {"type": "tumbler", "width": 600, "depth": 550, "height": 1200, "quantity": 1,
         "shelves": 1},
    ]),
    ("Стол мобильный 1000×600 H750", 20_300, "Офис", [
        {"type": "tumbler", "width": 1000, "depth": 600, "height": 750, "quantity": 1,
         "shelves": 1},
    ]),
]


def _per_kp_table() -> list[str]:
    payload = json.loads(SRC.read_text(encoding="utf-8"))
    lines = [
        "| КП | Позиций | Размеры в тексте | Медиана цены |",
        "|---|---:|---|---:|",
    ]
    for doc in payload["documents"]:
        path = Path("KP") / doc["file"]
        text = "\n".join(page.get_text() for page in pymupdf.open(path))
        dims = len(re.findall(r"\d{3,4}\s*[*хx×]\s*\d{2,4}", text))
        cash = sorted(p["cash"] for p in doc["pairs"] if p["cash"] > 0)
        median = cash[len(cash) // 2] if cash else 0
        mark = "да" if dims >= 3 else "нет"
        lines.append(
            f"| {doc['file'][:52]} | {len(doc['pairs'])} | {mark} ({dims}) | {median:,} |"
            .replace(",", " ")
        )
    return lines


def main() -> int:
    rows = []
    for name, kp, zone, modules in CASES:
        result = calculate_full_cost(
            modules, MATS, HW, [], zone_type=zone, price_book=PB
        )
        our = result.cost_breakdown.furniture_cash
        rows.append((name, kp, our, our - kp, (our - kp) / kp * 100))

    lines = [
        "# Сверка КП с расчётом конвейера (2026-11-17)",
        "",
        "## 1. По всем 22 КП: можно ли пересчитать",
        "",
        "Пересчёт возможен только при наличии габаритов и состава. Габариты",
        "считаются «есть», если в тексте КП найдено не меньше трёх пар размеров.",
        "",
        *_per_kp_table(),
        "",
        "Итог: размеры и количество есть **только в «волонтёрском центре»**.",
        "В остальных КП — фото, перечень материалов и цена; состав модулей",
        "(двери, ящики, полки) не указан, поэтому точная сверка невозможна.",
        "",
        "## 2. «Волонтёрский центр»: 8 позиций",
        "",
        "Состав (двери/ящики/полки) в КП не указан — принят по типу и ширине",
        "изделия. Габариты и количество — из КП. Цена КП — за единицу, без доставки.",
        "",
        "| Позиция | КП, ₽ | Конвейер, ₽ | Δ, ₽ | Δ, % |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, kp, our, delta, percent in rows:
        lines.append(
            f"| {name} | {kp:,} | {our:,.0f} | {delta:+,.0f} | {percent:+.1f} % |"
            .replace(",", " ")
        )

    lines += [
        "",
        "## 3. Что видно",
        "",
        "- Простые корпусные позиции сходятся: стол 1800×700 (+3,8 %), шкаф для",
        "  бумаг (−6,9 %), мобильный стол (+7,8 %).",
        "- Сложные и крупные позиции конвейер считает на 40–65 % дешевле:",
        "  кухня, шкаф 2000×2550, ресепшн, витрина. Причины, которые нужно",
        "  проверить по составу: не учтены фасады из ЛДСП, столешница, GOLA/LED,",
        "  ящики SMARTLINE (нет цены в прайсе), а также возможная разница",
        "  в коэффициентах для крупных изделий.",
        "- Фасады из ЛДСП в модели не попадают в расчёт вообще: `facade_type`",
        "  остаётся unknown. Это подтверждённый дефект, кандидат на исправление.",
        "",
        "Пересчёт: `scripts/kp_reconcile.py`.",
    ]

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[-14:]))
    print(f"\nСохранено: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
