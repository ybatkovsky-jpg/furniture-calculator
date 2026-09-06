"""
Тесты для расчёта кромки (app/services/edge_calc.py).

Чистые функции без I/O: пресеты кромления, метраж видимых/скрытых торцов,
стоимость по ценам за метр. Фиксирует текущее поведение
(включая граничные случаи) как регрессионный базлайн.
"""

from app.services.edge_calc import (
    EDGE_PRESETS,
    calculate_edge_for_detail,
    calculate_edge_for_modules,
)


# ════════════════════════════════════════════════════════════════════
# ОСНОВНОЙ ПУТЬ: пресеты и метраж
# ════════════════════════════════════════════════════════════════════

def test_kitchen_standard_only_visible_edges():
    """kitchen_standard: скрытая кромка None → считается только видимая (0.8мм).

    Деталь 600×800 → видимый периметр 2×(600+800)=2800мм = 2.8м.
    Цены не переданы → стоимость 0, метраж на месте.
    """
    result = calculate_edge_for_modules([{"details": [{"width_mm": 600, "height_mm": 800}]}])
    assert set(result.breakdown) == {"0.8"}
    assert result.breakdown["0.8"]["length_m"] == 2.8
    assert result.breakdown["0.8"]["cost"] == 0
    assert result.total_cost == 0


def test_kitchen_standard_with_prices():
    """Цены за метр: 2.8м × 100₽/м = 280₽."""
    modules = [{"details": [{"width_mm": 600, "height_mm": 800}]}]
    result = calculate_edge_for_modules(modules, edge_prices={"0.8": 100})
    assert result.breakdown["0.8"]["cost"] == 280.0
    assert result.total_cost == 280.0


def test_improved_preset_two_thicknesses():
    """improved: видимые 2мм + скрытые 0.4мм, оба по 2.8м."""
    modules = [{"details": [{"width_mm": 600, "height_mm": 800}]}]
    result = calculate_edge_for_modules(
        modules, edge_preset="improved",
        edge_prices={"2": 200, "0.4": 50},
    )
    assert set(result.breakdown) == {"2", "0.4"}
    assert result.breakdown["2"]["length_m"] == 2.8
    assert result.breakdown["0.4"]["cost"] == 140.0
    assert result.total_cost == 700.0


def test_premium_preset():
    """premium: видимые 2мм + скрытые 0.8мм."""
    modules = [{"details": [{"width_mm": 600, "height_mm": 800}]}]
    result = calculate_edge_for_modules(
        modules, edge_preset="premium",
        edge_prices={"2": 200, "0.8": 300},
    )
    assert set(result.breakdown) == {"2", "0.8"}
    assert result.total_cost == 560 + 840


# ════════════════════════════════════════════════════════════════════
# ДЕФОЛТЫ / FALLBACK
# ════════════════════════════════════════════════════════════════════

def test_unknown_preset_falls_back_to_kitchen_standard():
    """Неизвестный пресет → fallback на kitchen_standard (только 0.8мм)."""
    modules = [{"details": [{"width_mm": 600, "height_mm": 800}]}]
    result = calculate_edge_for_modules(modules, edge_preset="bogus")
    assert set(result.breakdown) == {"0.8"}
    assert result.breakdown["0.8"]["length_m"] == 2.8


def test_missing_price_defaults_to_zero_cost():
    """Отсутствующая цена для толщины → cost=0, но метраж сохраняется."""
    modules = [{"details": [{"width_mm": 600, "height_mm": 800}]}]
    result = calculate_edge_for_modules(
        modules, edge_preset="improved", edge_prices={"2": 200},
    )
    assert result.breakdown["2"]["cost"] == 560.0
    assert result.breakdown["0.4"]["cost"] == 0.0
    assert result.total_cost == 560.0


# ════════════════════════════════════════════════════════════════════
# ГРАНИЧНЫЕ СЛУЧАИ: нули / отрицательные размеры
# ════════════════════════════════════════════════════════════════════

def test_zero_and_negative_sizes_skipped_in_modules():
    """Длина ≤ 0 не попадает в breakdown (проверка length_mm > 0)."""
    modules = [{"details": [
        {"width_mm": 0, "height_mm": 0},
        {"width_mm": -900, "height_mm": 800},  # сумма −100 → длина −200мм < 0
    ]}]
    result = calculate_edge_for_modules(modules, edge_preset="improved")
    assert result.breakdown == {}
    assert result.total_cost == 0


def test_modules_without_details_skipped():
    """Модуль без ключа details пропускается; учтён только второй."""
    modules = [
        {"name": "модуль без деталей"},
        {"details": [{"width_mm": 600, "height_mm": 800}]},
    ]
    result = calculate_edge_for_modules(modules)
    assert result.breakdown["0.8"]["length_m"] == 2.8  # не 5.6


# ════════════════════════════════════════════════════════════════════
# РАСЧЁТ ДЛЯ ОДНОЙ ДЕТАЛИ (отдельная функция)
# ════════════════════════════════════════════════════════════════════

def test_calculate_edge_for_detail_improved():
    """Одна деталь, improved → оба пресета по 2.8м."""
    result = calculate_edge_for_detail(
        {"width_mm": 600, "height_mm": 800}, preset_name="improved",
    )
    assert result == {"2": 2.8, "0.4": 2.8}


def test_calculate_edge_for_detail_zero_size_still_returns_entry():
    """НЕСОГЛАСОВАННОСТЬ (зафиксирована): calculate_edge_for_detail НЕ фильтрует
    нулевую длину (в отличие от calculate_edge_for_modules) → {"0.8": 0.0}."""
    result = calculate_edge_for_detail({"width_mm": 0, "height_mm": 0})
    assert result == {"0.8": 0.0}
