"""
Тесты для расчётного движка.
"""

import pytest
from app.services.calc_engine import calculate_full_cost, CalculationResult
from app.services.cost_calc import DiscountInfo


def test_basic_calculation():
    """Тест базового расчёта."""
    # Тестовые данные
    modules = [
        {
            "type": "lower_base",
            "width_mm": 600,
            "height_mm": 820,
            "depth_mm": 560,
            "details": [
                {"width_mm": 596, "height_mm": 716, "material": "ЛДСП EGGER"},
                {"width_mm": 596, "height_mm": 100, "material": "ЛДСП EGGER"}
            ],
            "facades": [
                {"width_mm": 596, "height_mm": 716}
            ]
        }
    ]

    selected_materials = {
        "ldsp": {"id": 1, "name": "ЛДСП EGGER", "price": 5600},  # цена за лист
        "facades": {"price": 6800},  # цена за м²
        "edge": {"thickness": "0.8", "price": 50}  # цена за метр
    }

    selected_hardware = {
        "hinges": {"price": 150}  # цена за петлю
    }

    glass_items = []

    # Запуск расчёта
    result = calculate_full_cost(
        modules=modules,
        selected_materials=selected_materials,
        selected_hardware=selected_hardware,
        glass_items=glass_items
    )

    # Проверки
    assert isinstance(result, CalculationResult)
    assert result.total_cash > 0
    assert result.total_noncash > result.total_cash  # безнал > налички
    assert result.material_cost >= 0
    assert result.edge_cost >= 0
    assert result.facade_cost >= 0
    assert result.hardware_cost >= 0


def test_calculation_with_discount():
    """Тест расчёта со скидкой."""
    modules = [
        {
            "type": "lower_base",
            "width_mm": 600,
            "height_mm": 820,
            "depth_mm": 560,
            "details": [
                {"width_mm": 596, "height_mm": 716, "material": "ЛДСП EGGER"}
            ],
            "facades": [
                {"width_mm": 596, "height_mm": 716}
            ]
        }
    ]

    selected_materials = {
        "ldsp": {"price": 5600},
        "facades": {"price": 6800},
        "edge": {"thickness": "0.8", "price": 50}
    }

    selected_hardware = {
        "hinges": {"price": 150}
    }

    # Скидка 10%
    discount_info = DiscountInfo(
        discount_type="percent",
        discount_value=10
    )

    result = calculate_full_cost(
        modules=modules,
        selected_materials=selected_materials,
        selected_hardware=selected_hardware,
        glass_items=[],
        discount_info=discount_info
    )

    # Цена со скидкой должна быть меньше базовой
    assert result.total_cash < result.cost_breakdown.total_base_cash


def test_calculation_with_designer_bonus():
    """Тест расчёта с бонусом дизайнера."""
    modules = [
        {
            "type": "lower_base",
            "width_mm": 600,
            "height_mm": 820,
            "depth_mm": 560,
            "details": [
                {"width_mm": 596, "height_mm": 716, "material": "ЛДСП EGGER"}
            ],
            "facades": [
                {"width_mm": 596, "height_mm": 716}
            ]
        }
    ]

    selected_materials = {
        "ldsp": {"price": 5600},
        "facades": {"price": 6800},
        "edge": {"thickness": "0.8", "price": 50}
    }

    selected_hardware = {
        "hinges": {"price": 150}
    }

    # Бонус дизайнера 10%
    discount_info = DiscountInfo(
        designer_bonus_enabled=True,
        designer_bonus_rate=0.10
    )

    result = calculate_full_cost(
        modules=modules,
        selected_materials=selected_materials,
        selected_hardware=selected_hardware,
        glass_items=[],
        discount_info=discount_info
    )

    # Финальная цена должна быть больше базовой (бонус увеличивает цену)
    assert result.total_cash > result.cost_breakdown.total_base_cash
    assert result.cost_breakdown.designer_bonus_amount > 0