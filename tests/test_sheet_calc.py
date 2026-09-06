"""
Тесты для расчёта количества листов ЛДСП/МДФ (app/services/sheet_calc.py).

Чистые функции без I/O: calculate_sheets_for_modules и calculate_sheets_for_area.
Фиксирует текущее поведение (включая граничные случаи: нули, отрицательные
размеры, дефолты/фолбэки) как регрессионный базлайн.

Полезная площадь листа = 5.796 × 0.85 = 4.9266 м² (коэффициент 0.85).
"""

import math

import pytest

from app.services.sheet_calc import (
    SHEET_DIMENSIONS,
    SheetCalculation,
    calculate_sheets_for_area,
    calculate_sheets_for_modules,
)


# ════════════════════════════════════════════════════════════════════
# ГРАНИЧНЫЕ СЛУЧАИ: пустой ввод / пропуск модулей
# ════════════════════════════════════════════════════════════════════

def test_empty_modules_returns_zeroed_result():
    """Пустой список модулей → нулевой SheetCalculation, дефолты dataclass."""
    result = calculate_sheets_for_modules([])
    assert isinstance(result, SheetCalculation)
    assert result.total_sheets == 0
    assert result.total_area_m2 == 0
    assert result.breakdown == {}
    assert result.utilization_rate == 0.85


def test_modules_without_details_are_skipped():
    """Модуль без ключа "details" пропускается (continue) → нулевой результат."""
    modules = [
        {"name": "шкаф"},
        {"name": "полка", "foo": "bar"},
    ]
    result = calculate_sheets_for_modules(modules)
    assert result.total_sheets == 0
    assert result.breakdown == {}


def test_unknown_sheet_type_raises_keyerror():
    """Неизвестный sheet_type → KeyError (в SHEET_DIMENSIONS только "standard")."""
    with pytest.raises(KeyError):
        calculate_sheets_for_modules(
            [{"details": [{"material": "MDF", "width_mm": 100, "height_mm": 100}]}],
            sheet_type="eco",
        )
    with pytest.raises(KeyError):
        calculate_sheets_for_area(1.0, sheet_type="eco")


# ════════════════════════════════════════════════════════════════════
# АРИФМЕТИКА ЛИСТОВ: ceil(площадь / полезная_площадь)
# ════════════════════════════════════════════════════════════════════

def test_single_detail_one_sheet():
    """Одна деталь 1000×500мм = 0.5 м² < 4.9266 → 1 лист, cost=0 (цены не переданы)."""
    modules = [{"details": [{"material": "MDF", "width_mm": 1000, "height_mm": 500}]}]
    result = calculate_sheets_for_modules(modules)

    assert result.total_sheets == 1
    assert result.total_area_m2 == 0.5
    assert result.breakdown["MDF"] == {"sheets": 1, "area_m2": 0.5, "cost": 0}


def test_area_just_under_useful_is_one_sheet():
    """4.9 м² чуть меньше полезной (4.9266) → 1 лист."""
    modules = [{"details": [{"material": "MDF", "width_mm": 2800, "height_mm": 1750}]}]
    result = calculate_sheets_for_modules(modules)
    assert result.total_sheets == 1


def test_area_just_over_useful_is_two_sheets():
    """5.0 м² больше полезной (4.9266) → ceil(1.0149) = 2 листа."""
    modules = [{"details": [{"material": "MDF", "width_mm": 2500, "height_mm": 2000}]}]
    result = calculate_sheets_for_modules(modules)
    assert result.total_sheets == 2


def test_same_material_accumulates_across_modules():
    """Один материал в 3 модулях суммируется: 3 × 1.5 м² = 4.5 < 4.9266 → 1 лист.

    Если бы расчёт шёл по модулям отдельно, получилось бы 3 листа.
    """
    modules = [
        {"details": [{"material": "MDF", "width_mm": 1500, "height_mm": 1000}]},
        {"details": [{"material": "MDF", "width_mm": 1500, "height_mm": 1000}]},
        {"details": [{"material": "MDF", "width_mm": 1500, "height_mm": 1000}]},
    ]
    result = calculate_sheets_for_modules(modules)
    assert result.total_sheets == 1
    assert result.breakdown["MDF"]["sheets"] == 1
    assert result.total_area_m2 == 4.5


def test_multiple_materials_counted_separately():
    """Разные материалы считают листы независимо: 2.0 + 1.2 м² → по 1 листу."""
    modules = [
        {"details": [{"material": "MDF", "width_mm": 2000, "height_mm": 1000}]},   # 2.0 м²
        {"details": [{"material": "LDS", "width_mm": 1500, "height_mm": 800}]},    # 1.2 м²
    ]
    result = calculate_sheets_for_modules(modules)

    assert result.total_sheets == 2
    assert set(result.breakdown) == {"MDF", "LDS"}
    assert result.breakdown["MDF"]["sheets"] == 1
    assert result.breakdown["LDS"]["sheets"] == 1
    assert result.total_area_m2 == 3.2


# ════════════════════════════════════════════════════════════════════
# ДЕФОЛТЫ И ФОЛБЭКИ (отсутствующие ключи)
# ════════════════════════════════════════════════════════════════════

def test_missing_material_key_falls_back_to_unknown():
    """Деталь без "material" → попадает в бакет "unknown"."""
    modules = [{"details": [{"width_mm": 1000, "height_mm": 500}]}]
    result = calculate_sheets_for_modules(modules)

    assert "unknown" in result.breakdown
    assert result.breakdown["unknown"]["sheets"] == 1


def test_missing_dimensions_give_zero_area_detail():
    """Деталь без размеров → width_mm/height_mm по умолчанию 0 → площадь 0, 0 листов."""
    modules = [{"details": [{"material": "MDF"}]}]
    result = calculate_sheets_for_modules(modules)

    assert result.breakdown["MDF"] == {"sheets": 0, "area_m2": 0, "cost": 0}
    assert result.total_sheets == 0


def test_cost_uses_price_per_sheet_and_fallback_zero():
    """Стоимость = листы × цена за лист; материал без цены → cost=0 (fallback).

    MDF: 2×3.0 м² = 6.0 → 2 листа × 2500 = 5000
    LDS: 1.2 м² → 1 лист × 1800 = 1800
    HPL: 0.5 м² → 1 лист, цены нет → 0
    """
    modules = [
        {"details": [
            {"material": "MDF", "width_mm": 2000, "height_mm": 1500},
            {"material": "MDF", "width_mm": 2000, "height_mm": 1500},
            {"material": "LDS", "width_mm": 1500, "height_mm": 800},
            {"material": "HPL", "width_mm": 1000, "height_mm": 500},
        ]},
    ]
    result = calculate_sheets_for_modules(
        modules, material_prices={"MDF": 2500, "LDS": 1800}
    )

    assert result.breakdown["MDF"]["cost"] == 5000
    assert result.breakdown["LDS"]["cost"] == 1800
    assert result.breakdown["HPL"]["cost"] == 0
    assert result.total_sheets == 4


# ════════════════════════════════════════════════════════════════════
# ОТРИЦАТЕЛЬНЫЕ РАЗМЕРЫ (зафиксировано текущее поведение)
# ════════════════════════════════════════════════════════════════════

def test_negative_dimension_yields_zero_sheets():
    """Отрицательная ширина → отрицательная площадь: ceil(-0.5/4.9266)=0 листов.

    Площадь при этом сохраняется (-0.5) — защита от мусора отсутствует,
    поведение зафиксировано как есть.
    """
    modules = [{"details": [{"material": "MDF", "width_mm": -1000, "height_mm": 500}]}]
    result = calculate_sheets_for_modules(modules)

    assert result.total_sheets == 0
    assert result.breakdown["MDF"]["sheets"] == 0
    assert result.total_area_m2 == -0.5


# ════════════════════════════════════════════════════════════════════
# calculate_sheets_for_area: нули, отрицательные, округления
# ════════════════════════════════════════════════════════════════════

def test_zero_area_returns_zero_sheets_and_sheet_meta():
    """Площадь 0 → 0 листов; в ответе метаданные листа (5.796 и 5.796×0.85)."""
    result = calculate_sheets_for_area(0, material_price=100)

    assert result["sheets"] == 0
    assert result["area_m2"] == 0
    assert result["cost"] == 0
    assert result["sheet_area_m2"] == SHEET_DIMENSIONS["standard"]["area_m2"] == 5.796
    assert result["useful_area_m2"] == 5.796 * 0.85


def test_negative_area_yields_negative_sheet_count():
    """Отрицательная площадь → ceil(-5/4.9266) = -1 лист (защиты нет, зафиксировано)."""
    result = calculate_sheets_for_area(-5, material_price=100)

    assert result["sheets"] == math.ceil(-5 / (5.796 * 0.85)) == -1
    assert result["cost"] == -100


def test_area_cost_and_rounding():
    """Округления: area_m2 → 3 знака, cost → 2 знака; sheets = ceil(площадь/полезная)."""
    result = calculate_sheets_for_area(0.123456789, material_price=99.986)

    assert result["sheets"] == 1
    assert result["area_m2"] == 0.123
    assert result["cost"] == 99.99
