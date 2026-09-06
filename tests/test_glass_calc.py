"""
Тесты для расчёта стекла (app/services/glass_calc.py).

Чистые функции без I/O: фиксированные цены, ручные цены оператора,
тонировка, агрегация по модулям. Фиксирует текущее поведение
(включая граничные случаи) как регрессионный базлайн.
"""

from app.services.glass_calc import (
    GlassCalculation,
    GLASS_PRICES,
    calculate_glass_cost,
    calculate_glass_for_modules,
)


# ════════════════════════════════════════════════════════════════════
# ОСНОВНЫЕ ПУТИ: фиксированные цены
# ════════════════════════════════════════════════════════════════════

def test_clear_glass_area_and_cost():
    """clear 600×800мм → 0.48м² × 2500₽/м² = 1200₽."""
    result = calculate_glass_cost("clear", 600, 800)
    assert result["area_m2"] == 0.48
    assert result["cost"] == 1200.0
    assert result["glass_type"] == "clear"
    assert result["tinting"] is False


def test_for_aluminum_price():
    """for_aluminum 500×700мм → 0.35м² × 2230₽/м² = 780.5₽."""
    result = calculate_glass_cost("for_aluminum", 500, 700)
    assert result["area_m2"] == 0.35
    assert result["cost"] == 780.5


def test_moru_riflenoe_costs_one_sheet_regardless_of_size():
    """MORU считается листами: фиксированные 4250₽ даже при нулевых размерах."""
    normal = calculate_glass_cost("moru_riflenoe", 1000, 1500)
    assert normal["cost"] == GLASS_PRICES["moru_riflenoe"]

    zero = calculate_glass_cost("moru_riflenoe", 0, 0)
    assert zero["area_m2"] == 0.0
    assert zero["cost"] == 4250  # упрощение в коде: 1 лист на элемент


# ════════════════════════════════════════════════════════════════════
# ТОНИРОВКА
# ════════════════════════════════════════════════════════════════════

def test_tinting_adds_flat_fee():
    """Тонировка — фиксированные +2000₽ к стоимости элемента."""
    result = calculate_glass_cost("clear", 600, 800, tinting=True)
    assert result["cost"] == 1200 + GLASS_PRICES["tinting"]
    assert "тонировка" in result["description"]


# ════════════════════════════════════════════════════════════════════
# РУЧНЫЕ ЦЕНЫ / ТИПЫ БЕЗ ЦЕНЫ
# ════════════════════════════════════════════════════════════════════

def test_manual_price_used_for_unpriced_type():
    """Тип без фиксированной цены + ручная цена оператора → area × price."""
    result = calculate_glass_cost("graphite", 600, 800, manual_price=3000)
    assert result["cost"] == 0.48 * 3000


def test_unpriced_type_without_manual_price_costs_zero():
    """Тип без цены и без ручной цены → cost=0, пометка «нужна цена»."""
    result = calculate_glass_cost("tempered", 600, 800)
    assert result["cost"] == 0
    assert "нужна цена" in result["description"]


# ════════════════════════════════════════════════════════════════════
# ГРАНИЧНЫЕ СЛУЧАИ
# ════════════════════════════════════════════════════════════════════

def test_negative_dimensions_produce_negative_cost():
    """Отрицательный размер → отрицательная площадь/стоимость (защиты нет)."""
    result = calculate_glass_cost("clear", -600, 800)
    assert result["area_m2"] == -0.48
    assert result["cost"] == -1200.0


def test_rounding_of_area_and_cost():
    """Округление: area до 3 знаков, cost до 2."""
    # 1234×5678 = 7.006652м² → 7.007; ×2500₽/м² = 17516.63₽
    result = calculate_glass_cost("clear", 1234, 5678)
    assert result["area_m2"] == 7.007
    assert result["cost"] == 17516.63


# ════════════════════════════════════════════════════════════════════
# ДАННЫЕ ПО УМОЛЧАНИЮ / АГРЕГАЦИЯ ПО МОДУЛЯМ
# ════════════════════════════════════════════════════════════════════

def test_defaults_and_empty_aggregation():
    """GlassCalculation() и пустой список модулей → нули и пустой breakdown."""
    calc = GlassCalculation()
    assert calc.total_area_m2 == 0
    assert calc.total_cost == 0
    assert calc.breakdown == []

    result = calculate_glass_for_modules([])
    assert isinstance(result, GlassCalculation)
    assert result.breakdown == []
    assert result.total_cost == 0


def test_calculate_glass_for_modules_sums_and_skips():
    """Агрегация: модули без glass_items пропускаются, итоги суммируются."""
    modules = [
        {"glass_items": [
            {"type": "clear", "width_mm": 600, "height_mm": 800},
            {"type": "moru_riflenoe", "width_mm": 1000, "height_mm": 1500},
        ]},
        {"name": "модуль без стекла"},  # нет ключа glass_items
    ]
    result = calculate_glass_for_modules(modules)
    assert len(result.breakdown) == 2
    # Площадь суммируется по ВСЕМ элементам, включая MORU (0.48 + 1.5)
    assert result.total_area_m2 == 1.98
    assert result.total_cost == 1200 + 4250
