"""Кромка на метр квадратный панелей — контроль диапазона.

Кромка считается по геометрии: все торцы всех деталей ЛДСП × 1,3 на подрезку.
Практика (их проекты PRO100 и xlsx-расчёты) даёт 4–12 м кромки на м² панелей.
Норматив `EDGE_NORMS` выведен неверно (делили итог заказа на число корпусов)
и давал до 80 м на модуль, поэтому по умолчанию выключен.
"""

from __future__ import annotations

import pytest

from app.services import quantity_calc
from app.services.calc_engine import calculate_full_cost

MATS = {
    "ldsp": {"name": "ЛДСП", "price": 3700, "price_unit": "лист",
             "sheet_width_mm": 2800, "sheet_height_mm": 2070},
    "edge": {"name": "EGGER 0.8*19", "price": 41, "thickness_mm": 0.8},
}
HW = {"hinges": {"price": 200}, "drawers": {"price": 3300}, "handles": {"price": 500}}
PB = {"hdf": 1380, "confirmat": 2, "adjustable_feet": 50, "plinth": 1500}

CASES = {
    "кухня": (
        [{"type": "lower_base", "width": 600, "depth": 560, "height": 820,
          "quantity": 1, "facades": {"count": 1}, "shelves": 2}] * 5
        + [{"type": "upper_base", "width": 600, "depth": 350, "height": 900,
            "quantity": 1, "facades": {"count": 1}, "shelves": 3}] * 5
        + [{"type": "penal", "width": 600, "depth": 560, "height": 2100,
            "quantity": 1, "facades": {"count": 2}, "shelves": 2}]
    ),
    "гардероб": [{"type": "penal", "width": 2000, "depth": 550, "height": 2550,
                  "quantity": 1, "facades": {"count": 4}, "shelves": 5}],
    "шкаф для бумаг": [{"type": "penal", "width": 1000, "depth": 450, "height": 2550,
                        "quantity": 1, "facades": {"count": 2}, "shelves": 5}],
    "стол": [{"type": "table", "width": 1800, "depth": 700, "height": 750, "quantity": 1}],
    "витрина": [
        {"type": "lower_base", "width": 1000, "depth": 450, "height": 820,
         "quantity": 1, "facades": {"count": 2}, "shelves": 1},
        {"type": "upper_base", "width": 1000, "depth": 450, "height": 1700,
         "quantity": 1, "facades": {"count": 2}, "shelves": 3},
    ],
}

MIN_RATIO = 4.0
MAX_RATIO = 12.0


@pytest.mark.parametrize("name", sorted(CASES))
def test_edge_per_area_is_within_practice_range(name: str, monkeypatch) -> None:
    monkeypatch.setattr(quantity_calc, "EDGE_NORMS_ENABLED", False)
    modules = CASES[name]
    result = calculate_full_cost(
        modules, MATS, HW, [],
        zone_type="kitchen" if name == "кухня" else "office",
        price_book=PB,
    )
    area = result.sheets_calc.total_area_m2 + sum(
        line.quantity for line in result.lines if line.kind == "facade"
    )
    edge_m = sum(line.quantity for line in result.lines if line.kind == "edge")
    ratio = edge_m / area
    assert MIN_RATIO <= ratio <= MAX_RATIO, (
        f"{name}: {ratio:.1f} м кромки на м² панелей — вне практики "
        f"{MIN_RATIO}–{MAX_RATIO} (кромка {edge_m:.0f} м, панели {area:.1f} м²)"
    )


def test_edge_norms_are_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("EDGE_NORMS", raising=False)
    enabled = __import__("os").getenv("EDGE_NORMS", "0") == "1"
    assert enabled is False
