"""
Parity петель: единое правило hinges_per_door для обоих потоков
(quantity_calc — Excel-смета AI-пайплайна, hardware_calc/calc_engine — КП/бот).
"""

import pytest
from app.services.hardware_calc import (
    hinges_per_door,
    calculate_hinges_for_modules,
    estimate_facade_weight_kg,
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


# ════════════════════════════════════════════════════════════════════
# BLUM/HETTICH: высотно-весовая надбавка (вес фасада — ОПЦИОНАЛЬНЫЙ вход)
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("height,weight,expected", [
    # 0/None — вес неизвестен → чисто высотная таблица (как раньше)
    (850, None, 2), (850, 0, 2), (1000, None, 3), (1500, None, 4), (1800, None, 5),
    # <900 мм: 2 базовых → 3, если >18 кг
    (850, 18, 2), (850, 19, 3), (850, 25, 3),
    # 900–1600 мм: 3–4 базовых → +1, если >20 кг
    (1000, 20, 3), (1000, 21, 4), (1500, 20, 4), (1500, 22, 5),
    # >1600 мм: 5 базовых → 6, если >20 кг
    (1800, 20, 5), (1800, 25, 6), (2000, 22, 6), (2500, 30, 6),
    # ровно 1600: высотная база 4 (1200..1600) → 5 при >20 кг
    (1600, 22, 5), (1600, 18, 4),
])
def test_hinges_per_door_blum_height_weight(height, weight, expected):
    """BLUM: высота × вес — тяжёлый фасад при той же высоте даёт +1 петлю."""
    assert hinges_per_door(height, brand="BLUM", door_weight_kg=weight) == expected
    assert hinges_per_door(height, brand="HETTICH", door_weight_kg=weight) == expected


def test_hinges_per_door_blum_weight_boundaries_exact():
    """Пороги — строгие: ровно 18/20 кг НЕ дают надбавку, только >."""
    assert hinges_per_door(850, "BLUM", 18.0) == 2
    assert hinges_per_door(850, "BLUM", 18.01) == 3
    assert hinges_per_door(1500, "BLUM", 20.0) == 4
    assert hinges_per_door(1500, "BLUM", 20.01) == 5


def test_hinges_per_door_firmax_ignores_weight():
    """«Правила заказчика»: FIRMAX считается ТОЛЬКО по высоте, вес не влияет."""
    assert hinges_per_door(2100, "FIRMAX", 30) == 4     # ≥2000 → 4 (как без веса)
    assert hinges_per_door(800, "FIRMAX", 25) == 2      # ≤900 → 2
    assert hinges_per_door(1000, "FIRMAX", 30) == 3     # 901..1999 → 3


def test_estimate_facade_weight_kg():
    """Оценка веса фасада: объём (м³) × плотность (кг/м³)."""
    # МДФ 18мм: 0.6 × 2.5 × 0.018 м³ × 780 кг/м³ = 21.06 кг
    assert estimate_facade_weight_kg(600, 2500, material="mdf") == pytest.approx(21.06)
    # ЛДСП 16мм: 0.6 × 0.82 × 0.016 × 700 = 5.51 кг
    assert estimate_facade_weight_kg(600, 820, material="ldsp") == pytest.approx(5.51)
    # стекло 4мм (типовая) и явная плотность
    assert estimate_facade_weight_kg(600, 820, material="glass") == pytest.approx(4.92)
    assert estimate_facade_weight_kg(600, 820, 18, 780) == pytest.approx(6.91)
    # неизвестный материал → вес неизвестен (None), без данных → None
    assert estimate_facade_weight_kg(600, 820, material="чипборд") is None
    assert estimate_facade_weight_kg(None, 820, material="mdf") is None
    assert estimate_facade_weight_kg(600, 820) is None


def test_hinges_heavy_vs_no_weight_more_hinges():
    """Тяжёлый фасад BLUM (>20 кг при 1600–2000 мм) → петель БОЛЬШЕ, чем без веса."""
    for h in (1700, 1800, 1900, 2000):
        without_weight = hinges_per_door(h, brand="BLUM")
        heavy = hinges_per_door(h, brand="BLUM", door_weight_kg=25)
        assert heavy > without_weight


def _hw_mod(facades):
    """Модуль для calculate_hinges_for_modules с фасадами."""
    return {"facades": facades}


def test_parity_quantity_calc_vs_hardware_calc_blum_with_weight():
    """Parity (г): quantity_calc-путь и hardware_calc-путь дают одинаковый результат
    на одном наборе дверей BLUM с известным весом фасада.

    Двери «на заказ» из МДФ 18мм (EMDIWAY → facade_type=emdiway → материал mdf):
    - пенал 600×2500: вес ≈ 0.6×2.5×0.018×780 = 21.06 кг > 20 → 6 петель;
    - низ 600×820:     вес ≈ 0.6×0.82×0.018×780 = 6.9 кг < 18 → 2 петли.
    Оба потока должны насчитать 6 + 2 = 8 петель."""
    mods = [
        _mk("penal", 600, 2500),
        _mk("lower_base", 600, 820),
    ]
    q = calculate_quantities(
        mods, "Кухня", ["EMDIWAY U702 ST9"], zone_type="kitchen",
        auto_accessories=False, auto_drawers=False, auto_led=False,
        hinge_brand="BLUM",
    )

    hw = calculate_hinges_for_modules(
        [
            _hw_mod([{"width_mm": 600, "height_mm": 2500, "material": "mdf"}]),
            _hw_mod([{"width_mm": 600, "height_mm": 820, "material": "mdf"}]),
        ],
        brand="BLUM",
    )

    assert q.hinges_count == 8
    assert hw["total_hinges"] == 8
    assert q.hinges_count == hw["total_hinges"]


def test_parity_blum_with_weight_heavy_only_in_both_paths():
    """Один и тот же тяжёлый фасад (>20 кг) — и quantity_calc, и hardware_calc
    дают 6 петель (5 по высоте + 1 за вес), а без веса было бы 5."""
    mods = [_mk("penal", 600, 2500)]
    q = calculate_quantities(
        mods, "Кухня", ["EMDIWAY U702 ST9"], zone_type="kitchen",
        auto_accessories=False, auto_drawers=False, auto_led=False,
        hinge_brand="BLUM",
    )
    q_no_weight = calculate_quantities(
        mods, "Кухня", [], zone_type="kitchen",
        auto_accessories=False, auto_drawers=False, auto_led=False,
        hinge_brand="BLUM",
    )
    hw = calculate_hinges_for_modules(
        [_hw_mod([{"width_mm": 600, "height_mm": 2500, "material": "mdf"}])],
        brand="BLUM",
    )
    hw_no_weight = calculate_hinges_for_modules(
        [_hw_mod([{"height_mm": 2500}])], brand="BLUM",
    )

    assert q_no_weight.hinges_count == 5        # высотная таблица BLUM (>1600 → 5)
    assert q.hinges_count == 6                  # +1 за вес >20 кг
    assert hw_no_weight["total_hinges"] == 5
    assert hw["total_hinges"] == 6
    assert q.hinges_count == hw["total_hinges"]


def test_firmax_quantity_path_ignores_weight_with_mdf_materials():
    """FIRMAX (правила заказчика): тот же набор дверей из МДФ с весом считается
    строго по высоте: пенал 2500 → 4, низ 820 → 2, итого 6 (вес не влияет)."""
    mods = [_mk("penal", 600, 2500), _mk("lower_base", 600, 820)]
    q = calculate_quantities(
        mods, "Кухня", ["EMDIWAY U702 ST9"], zone_type="kitchen",
        auto_accessories=False, auto_drawers=False, auto_led=False,
        hinge_brand="FIRMAX",
    )
    hw = calculate_hinges_for_modules(
        [
            _hw_mod([{"width_mm": 600, "height_mm": 2500, "material": "mdf"}]),
            _hw_mod([{"width_mm": 600, "height_mm": 820, "material": "mdf"}]),
        ],
        brand="FIRMAX",
    )
    assert q.hinges_count == 6      # 4 (2500) + 2 (820)
    assert hw["total_hinges"] == 6
    assert q.hinges_count == hw["total_hinges"]


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
