"""Сверка наших количеств и цены с проектами PRO100 и КП.

1. Считает эталонную кухню нашим движком (правила раскладки) и выводит
   площадь панелей, петли, ящики, цену — базу для сравнения.
2. Для пар «проект → КП» считает цену КП на м² площади панелей проекта.

Запуск: .\\.venv\\Scripts\\python.exe scripts/kp_vs_project.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from kp_compose_rules import HW, MATS, PB, compose

from app.services.calc_engine import calculate_full_cost

QUANTITIES = Path("docs/pro100_quantities.json")
OUT = Path("docs/kp_vs_project.json")

# Пары «проект PRO100 → КП» берём из локального файла, чтобы имена клиентов
# не попадали в Git: {"pairs": [{"project": "...", "kp": "...", "price": 0}, ...]}.
PAIRS_FILE = Path("docs/kp_vs_project_pairs.json")


def load_pairs() -> list[tuple[str, str, int]]:
    if not PAIRS_FILE.exists():
        return []
    data = json.loads(PAIRS_FILE.read_text(encoding="utf-8"))
    return [(p["project"], p["kp"], p["price"]) for p in data["pairs"]]


def our_reference() -> dict:
    modules, zone = compose("Кухонный гарнитур 3000×1700 H2550", 3000, 1700, 2550)
    result = calculate_full_cost(
        modules, MATS, HW, [], zone_type=zone, price_book=PB,
        options={"accessories": True, "gola": True, "led": True},
    )
    # Площадь панелей: из раскроя (м²) + площадь фасадов (м²).
    area = float(getattr(result.sheets_calc, "total_area_m2", 0.0) or 0.0)
    area += sum(
        line.quantity for line in result.lines if line.kind == "facade"
    )
    return {
        "area_m2": round(area, 2),
        "total_cash": round(result.total_cash),
        "hinges": sum(l.quantity for l in result.lines if l.kind == "hinge"),
        "drawers": sum(l.quantity for l in result.lines if l.kind == "drawer"),
        "price_per_m2": round(result.total_cash / area) if area else 0,
    }


def main() -> int:
    reference = our_reference()
    print("наш расчёт кухни 3000×1700:")
    print(f"  площадь панелей: {reference['area_m2']} м²")
    print(f"  цена наличными: {reference['total_cash']:,} ₽".replace(",", " "))
    print(f"  петли: {reference['hinges']:.0f}, ящики: {reference['drawers']:.0f}")
    print(f"  цена за м² панелей: {reference['price_per_m2']:,} ₽".replace(",", " "))

    quantities = {
        row["project"]: row
        for row in json.loads(QUANTITIES.read_text(encoding="utf-8"))
    }

    rows = []
    print(f"\n{'проект':28} {'площадь':>8} {'петли':>6} {'КП':>9} {'₽/м²':>8}")
    for project, kp, price in load_pairs():
        data = quantities.get(project)
        if not data:
            print(f"{project[:28]:28} нет данных")
            continue
        area = data["area_m2"]
        per_m2 = round(price / area) if area else 0
        rows.append({
            "project": project, "kp": kp, "kp_price": price,
            "area_m2": area, "hinges": data["hinges"],
            "drawers": data["drawers"], "price_per_m2": per_m2,
        })
        print(f"{project[:28]:28} {area:>8} {data['hinges']:>6} {price:>9} {per_m2:>8}")

    OUT.write_text(
        json.dumps({"reference": reference, "pairs": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nСохранено: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
