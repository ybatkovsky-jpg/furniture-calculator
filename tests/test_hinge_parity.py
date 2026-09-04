"""
Parity петель: единое правило hinges_per_door для обоих потоков
(quantity_calc — Excel-смета AI-пайплайна, hardware_calc/calc_engine — КП/бот).
"""

import pytest
from app.services.hardware_calc import (
    hinges_per_door,
    calculate_hinges_for_modules,
    DEFAULT_HINGE_BRAND,
)
from app.services.quantity_calc import calculate_quantities
from app.services.image_analyzer import RecognizedModule


@pytest.mark.parametrize("height,expected", [
    (0, 2), (600, 2), (900, 2), (901, 3), (1200, 3), (1999, 3),
    (2000, 4), (2100, 4), (2700, 4),
])
def test_hinges_per_door_firmax(height, expected):
    # «Правила заказчика»: ≥2000 → 4; 901..1999 → 3; ≤900 → 2
    assert hinges_per_door(height, brand="FIRMAX") == expected


@pytest.mark.parametrize("height,expected", [
    (0, 2), (899, 2), (900, 3), (1199, 3), (1200, 4), (1600, 4),
    (1601, 5), (2200, 5),
])
def test_hinges_per_door_blum(height, expected):
    # Высотная таблица (без веса): >1600 → 5; 1200..1600 → 4; 900..1200 → 3; <900 → 2
    assert hinges_per_door(height, brand="BLUM") == expected


def test_hinges_per_door_unknown_brand_falls_back_to_firmax():
    assert DEFAULT_HINGE_BRAND == "FIRMAX"
    assert hinges_per_door(2100, brand="SOMETHING") == 4   # FIRMAX ≥2000 → 4
    assert hinges_per_door(1050) == 3                       # дефолт: FIRMAX 901..1999 → 3


def _mk(t, w, h, q=1, fc=1):
    return RecognizedModule(
        type=t, width=w, depth=560, height=h, quantity=q, is_corner=False,
        facades={"count": fc, "type": "doors"},
    )


def test_parity_quantity_calc_vs_hardware_calc():
    """Один и тот же набор дверей → одинаковое число петель в обоих потоках."""
    mods = [
        _mk("lower_base", 600, 820, q=3),      # 3 нижних по 820
        _mk("penal", 600, 2100),               # пенал 2100
        _mk("upper_base", 600, 720, q=2),      # 2 верхних по 720
        _mk("lower_base", 1200, 820, fc=2),    # широкий низ: 2 двери рядом по 820
        _mk("column", 600, 1700),              # колонна 1700
    ]
    q = calculate_quantities(
        mods, "Кухня", [], zone_type="kitchen",
        auto_accessories=False, auto_drawers=False, auto_led=False,
    )

    hw_mods = [{"facades": [{"height_mm": 820}]} for _ in range(3)]
    hw_mods.append({"facades": [{"height_mm": 2100}]})
    hw_mods.extend({"facades": [{"height_mm": 720}]} for _ in range(2))
    hw_mods.append({"facades": [{"height_mm": 820}, {"height_mm": 820}]})
    hw_mods.append({"facades": [{"height_mm": 1700}]})

    hw = calculate_hinges_for_modules(hw_mods, brand="FIRMAX")

    # 6 (нижние) + 4 (пенал) + 4 (верхние) + 4 (широкий) + 3 (колонна) = 21
    assert q.hinges_count == 21
    assert hw["total_hinges"] == 21
    assert q.hinges_count == hw["total_hinges"]
