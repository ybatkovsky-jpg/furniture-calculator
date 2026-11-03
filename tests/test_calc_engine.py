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


# ════════════════════════════════════════════════════════════════════
# Мост схем: ручной ввод (filling+quantity) ↦ RecognizedModule
# ════════════════════════════════════════════════════════════════════

def _prices():
    return (
        {"ldsp": {"name": "LAMARTY", "price": 5600},
         "facades": {"price": 10000},
         "edge": {"price": 50}},
        {"hinges": {"price": 200}},
    )


def test_manual_doors_do_not_multiply_carcass():
    """Ручная схема: filling="doors", quantity=2 — это ДВЕ ДВЕРИ одного пенала.

    Раньше quantity уходила в RecognizedModule как число корпусов: корпус
    удваивался (лишние листы ДСП), а фасады вообще не считались.
    """
    materials, hardware = _prices()
    module = {
        "type": "penal", "width": 800, "depth": 320, "height": 2100,
        "filling": "doors", "quantity": 2, "has_glass": False,
    }
    one_door = dict(module, quantity=1)

    two = calculate_full_cost([module], materials, hardware, [])
    one = calculate_full_cost([one_door], materials, hardware, [])

    # корпус один и тот же — листы не зависят от числа дверей
    assert two.sheets_calc.total_sheets == one.sheets_calc.total_sheets
    # фасады и фурнитура посчитаны (раньше здесь был 0 ₽ и 0 петель)
    assert two.facade_cost > 0
    assert two.hardware_calc.hinges_total == 8      # 2 двери × 4 петли (FIRMAX, 2100)
    assert two.hardware_calc.handles_total == 4     # высокая дверь → 2 ручки
    assert one.hardware_calc.hinges_total == 4


def test_manual_drawers_get_handles_without_hinges():
    """Ручная схема: filling="drawers", quantity=3 — ящики без дверей.

    Каждый выдвижной фасад имеет ручку; петель у ящиков нет.
    """
    materials, hardware = _prices()
    module = {
        "type": "lower_base", "width": 450, "depth": 560, "height": 820,
        "filling": "drawers", "quantity": 3,
    }
    result = calculate_full_cost([module], materials, hardware, [])

    assert result.hardware_calc.drawers_total == 3
    assert result.hardware_calc.hinges_total == 0
    assert result.hardware_calc.handles_total == 3
    assert result.facade_cost == 0          # дверей нет — фасадов нет


def test_dict_to_recognized_manual_schema():
    """filling="shelves": quantity → полки, корпус один, фасадов нет."""
    from app.services.calc_engine import _dict_to_recognized_module

    m = _dict_to_recognized_module({
        "type": "penal", "width": 600, "depth": 320, "height": 2100,
        "filling": "shelves", "quantity": 6,
    })
    assert (m.width, m.depth, m.height) == (600, 320, 2100)
    assert m.quantity == 1
    assert m.shelves == 6
    assert m.facades is None and m.drawers is None


def test_dict_to_recognized_legacy_width_mm_and_facade_list():
    """Старый формат: размеры с суффиксом _mm, facades — список фасадов."""
    from app.services.calc_engine import _dict_to_recognized_module

    m = _dict_to_recognized_module({
        "type": "lower_base", "width_mm": 600, "depth_mm": 560, "height_mm": 820,
        "facades": [{"width_mm": 596, "height_mm": 716}],
    })
    assert (m.width, m.depth, m.height) == (600, 560, 820)
    assert m.facades == {"count": 1, "type": "doors"}
    assert m.quantity == 1


def test_dict_to_recognized_vision_schema_unchanged():
    """Схема распознавания: quantity остаётся числом корпусов, фасады не трогаем."""
    from app.services.calc_engine import _dict_to_recognized_module

    m = _dict_to_recognized_module({
        "type": "lower_base", "width": 600, "depth": 560, "height": 716,
        "quantity": 7, "facades": {"count": 1, "type": "doors"},
        "drawers": {"count": 2}, "shelves": 1,
    })
    assert m.quantity == 7
    assert m.facades == {"count": 1, "type": "doors"}
    assert m.drawers == {"count": 2}
    assert m.shelves == 1