"""Опции из текста КП → явные флаги расчёта.

Собираем состав по правилам (габариты из КП) и включаем то, что перечислено
в материалах позиции: сушка, лоток, поддон, GOLA, LED, столешница.

Запуск: .\\.venv\\Scripts\\python.exe scripts/kp_options_eval.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.calc_engine import calculate_full_cost

OUT = Path("docs/kp_options_eval.json")

MATS = {
    "ldsp": {"name": "ТОМЛЕСДРЕВ", "price": 3700, "price_unit": "лист"},
    "facades": {"name": "ЛДСП Томлесдрев", "price": 3700, "price_unit": "лист",
                "sheet_width_mm": 2800, "sheet_height_mm": 2070},
    "edge": {"name": "EGGER 0.8*19", "price": 34, "thickness_mm": 0.8},
}
HW = {"hinges": {"price": 140, "brand": "FIRMAX"},
      "drawers": {"price": 915}, "handles": {"price": 300}}
PB = {"hdf": 1530, "confirmat": 2, "adjustable_feet": 50, "plinth": 950,
      "edge_0.4": 17.5, "countertop": 3500,
      "drying_rack": 1700, "drying_rack_boyard": 3500,
      "cutlery_tray": 2400, "sink_pad": 1700,
      "gola_vertical": 1835, "gola_horizontal": 1200, "led_strip": 1000,
      "led_profile": 1651}

OPTION_WORDS = {
    "accessories": ("сушк", "лоток", "поддон", "бутылочн"),
    "gola": ("gola", "ручка-профиль", "ручка профиль"),
    "led": ("led", "подсветк"),
}


def parse_options(text: str) -> dict:
    lowered = text.casefold()
    return {
        key: any(word in lowered for word in words)
        for key, words in OPTION_WORDS.items()
    }


def compose(title: str, width: int, depth: int, height: int) -> tuple[list[dict], str]:
    text = title.casefold()
    modules: list[dict] = []
    if "кух" in text:
        count = max(2, round(width / 600))
        each = round(width / count)
        for index in range(count):
            if index % 4 == 3:
                modules.append({"type": "lower_base", "width": each, "depth": min(depth, 560),
                                "height": 820, "quantity": 1, "drawers": {"count": 3}})
            else:
                modules.append({"type": "lower_base", "width": each, "depth": min(depth, 560),
                                "height": 820, "quantity": 1,
                                "facades": {"count": 1}, "shelves": 1})
        for _ in range(count):
            modules.append({"type": "upper_base", "width": each, "depth": 300,
                            "height": 720, "quantity": 1,
                            "facades": {"count": 1}, "shelves": 1})
        if height > 2000:
            modules.append({"type": "penal", "width": 600, "depth": min(depth, 560),
                            "height": 2100, "quantity": 1,
                            "facades": {"count": 2}, "shelves": 2})
        return modules, "Кухня"
    if "стол" in text:
        modules.append({"type": "table", "width": width, "depth": depth,
                        "height": min(height, 750), "quantity": 1})
        if "тумб" in text:
            modules.append({"type": "drawer_unit", "width": 400, "depth": depth,
                            "height": min(height, 750), "quantity": 1,
                            "drawers": {"count": 3}})
        return modules, "Офис"
    if "ресепшн" in text or "стойк" in text:
        modules.append({"type": "counter", "width": width, "depth": depth,
                        "height": height, "quantity": 1,
                        "facades": {"count": 3}, "drawers": {"count": 3}})
        return modules, "Офис"
    if "витрин" in text:
        modules.append({"type": "lower_base", "width": width, "depth": depth,
                        "height": 820, "quantity": 1,
                        "facades": {"count": 2}, "shelves": 1})
        modules.append({"type": "upper_base", "width": width, "depth": depth,
                        "height": max(height - 820, 600), "quantity": 1,
                        "facades": {"count": 2, "type": "glass"}, "shelves": 3,
                        "has_glass": True})
        return modules, "Офис"
    if "шкаф" in text or "стеллаж" in text:
        doors = max(2, round(width / 500))
        modules.append({"type": "penal", "width": width, "depth": depth, "height": height,
                        "quantity": 1, "facades": {"count": doors}, "shelves": 5})
        return modules, "Гардеробная" if "одежд" in text else "Офис"
    if "трибун" in text:
        modules.append({"type": "open_unit", "width": width, "depth": depth,
                        "height": height, "quantity": 1, "shelves": 2})
        return modules, "Офис"
    modules.append({"type": "open_unit", "width": width, "depth": depth,
                    "height": height, "quantity": 1, "shelves": 1})
    return modules, "Офис"


ITEMS = [
    ("Кухонный гарнитур 3000×1700 H2550", 3000, 1700, 2550, 1, 375_000,
     ("сушка для посуды, лоток для столовых приборов, гигиенический поддон, "
      "ручка-профиль Gola, LED подсветка, цоколь ПВХ")),
    ("Шкаф большой под одежду 2000×550 H2550", 2000, 550, 2550, 1, 159_000,
     "штанга овальная, ручка торцевая"),
    ("Ресепшн 1800×700 H1100", 1800, 700, 1100, 1, 149_000,
     "направляющие скрытого монтажа, ручка торцевая"),
    ("Стол рабочий 1800×700 H1100", 1800, 700, 1100, 8, 45_700,
     "направляющие скрытого монтажа"),
    ("Шкаф для бумаг 1000×450 H2550", 1000, 450, 2550, 5, 54_000,
     "петли, ручка торцевая"),
    ("Стеллаж-витрина 1000×450 H2550", 1000, 450, 2550, 2, 109_000,
     "стекло тонированное, LED подсветка, профиль для стекла"),
    ("Трибуна 600×550 H1200", 600, 550, 1200, 1, 46_200, "корпус ЛДСП"),
    ("Стол мобильный 1000×600 H750", 1000, 600, 750, 6, 20_300,
     "колесо с тормозом"),
]


def main() -> int:
    rows = []
    print(f"{'позиция':40} {'опции':22} {'КП':>8} {'наш':>8} {'Δ%':>7}")
    for title, width, depth, height, qty, kp_unit, materials in ITEMS:
        options = parse_options(materials)
        modules, zone = compose(title, width, depth, height)
        result = calculate_full_cost(
            modules, MATS, HW, [], zone_type=zone, price_book=PB, options=options
        )
        our_unit = round(result.total_cash)
        delta = (our_unit - kp_unit) / kp_unit * 100 if kp_unit else 0
        flags = ",".join(key for key, value in options.items() if value) or "—"
        rows.append({"title": title, "qty": qty, "kp_unit": kp_unit,
                     "our_unit": our_unit, "delta": round(delta, 1),
                     "options": options})
        print(f"{title[:40]:40} {flags[:22]:22} {kp_unit:>8} {our_unit:>8} {delta:>+6.1f}%")
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСохранено: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
