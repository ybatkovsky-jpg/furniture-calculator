"""
Тесты для масштабного расчёта фасадов по bbox-процентам.

Покрывают calculate_scaled_facades — ядро метода «размерная линия → bbox → Python
считает точные мм». Чистая Python-функция, без вызова моделей.
Фиксирует текущее поведение (включая граничные случаи) как регрессионный базлайн.
"""

import pytest
from app.services.scale_calc import (
    calculate_scaled_facades,
    ScaledFacade,
    DEFAULT_HEIGHTS,
)


# ════════════════════════════════════════════════════════════════════
# ГРАНИЧНЫЕ СЛУЧАИ: пустой ввод / нули
# ════════════════════════════════════════════════════════════════════

def test_empty_facades_returns_empty():
    """Пустой список фасадов → пустой результат."""
    result = calculate_scaled_facades([], total_width_mm=3000)
    assert result == []


def test_zero_total_width_returns_empty():
    """total_width_mm=0 → масштаб не считается, пустой результат."""
    result = calculate_scaled_facades(
        [{"zone": "lower", "bbox_x_pct": 0, "bbox_w_pct": 20}],
        total_width_mm=0,
    )
    assert result == []


def test_negative_total_width_returns_empty():
    """Отрицательный габарит → пустой результат (защита от мусора)."""
    result = calculate_scaled_facades(
        [{"zone": "lower", "bbox_w_pct": 20}],
        total_width_mm=-100,
    )
    assert result == []


def test_all_zero_bbox_returns_empty():
    """Все bbox_w_pct=0 → сумма 0 → масштаб невозможен."""
    result = calculate_scaled_facades(
        [{"zone": "lower", "bbox_w_pct": 0}, {"zone": "lower", "bbox_w_pct": 0}],
        total_width_mm=3000,
    )
    assert result == []


# ════════════════════════════════════════════════════════════════════
# АРИФМЕТИКА МАСШТАБА (ядро метода)
# ════════════════════════════════════════════════════════════════════

def test_simple_scale_arithmetic():
    """Базовая арифметика: total=3000, 3 фасада по 20% → каждый 1000мм.

    scale = 3000 / 60 = 50 мм/процент
    width = 20 × 50 = 1000мм
    """
    facades = [
        {"zone": "lower", "bbox_x_pct": 0, "bbox_w_pct": 20},
        {"zone": "lower", "bbox_x_pct": 25, "bbox_w_pct": 20},
        {"zone": "lower", "bbox_x_pct": 50, "bbox_w_pct": 20},
    ]
    result = calculate_scaled_facades(facades, total_width_mm=3000)

    assert len(result) == 3
    assert all(r.width_mm == 1000 for r in result)
    assert all(r.height_mm == DEFAULT_HEIGHTS["lower"] for r in result)


def test_uneven_widths():
    """Фасады разной ширины: scale=60мм/%, ширину округляет через round()."""
    # total=3000, lower сумма = 50% → scale = 60 мм/%
    facades = [
        {"zone": "lower", "bbox_w_pct": 10},   # → 600мм
        {"zone": "lower", "bbox_w_pct": 15},   # → 900мм
        {"zone": "lower", "bbox_w_pct": 25},   # → 1500мм (но >1200 → корректируется)
    ]
    result = calculate_scaled_facades(facades, total_width_mm=3000)
    assert result[0].width_mm == 600
    assert result[1].width_mm == 900
    assert result[2].width_mm == 1000  # 1500 → корректировка до 1000


def test_scale_uses_only_lower_facades():
    """Масштаб считается ТОЛЬКО по lower-фасадам; upper/penal в сумму не входят."""
    # 3 lower по 10% = 30% → scale = 3000/30 = 100 мм/%
    # upper 9% → 900мм; penal 10% → 1000мм
    facades = [
        {"zone": "lower", "bbox_w_pct": 10},   # 1000мм
        {"zone": "lower", "bbox_w_pct": 10},   # 1000мм
        {"zone": "lower", "bbox_w_pct": 10},   # 1000мм
        {"zone": "upper", "bbox_w_pct": 9},    # 900мм
        {"zone": "penal", "bbox_w_pct": 10},   # 1000мм
    ]
    result = calculate_scaled_facades(facades, total_width_mm=3000)
    assert len(result) == 5
    lowers = [r for r in result if r.zone == "lower"]
    assert all(r.width_mm == 1000 for r in lowers)
    uppers = [r for r in result if r.zone == "upper"]
    assert uppers[0].width_mm == 900


def test_no_lower_facades_uses_all_for_scale():
    """Если lower нет — масштаб считается по ВСЕМ фасадам (fallback)."""
    # 2 upper по 25% = 50% → scale = 2000/50 = 40 мм/%
    facades = [
        {"zone": "upper", "bbox_w_pct": 25},
        {"zone": "upper", "bbox_w_pct": 25},
    ]
    result = calculate_scaled_facades(facades, total_width_mm=2000)
    assert len(result) == 2
    assert all(r.width_mm == 1000 for r in result)


# ════════════════════════════════════════════════════════════════════
# КОРРЕКТИРОВКА ГРАНИЦ (защита от гигантов/лилипутов)
# ════════════════════════════════════════════════════════════════════

def test_too_narrow_corrected_to_250():
    """Ширина <200мм → корректируется до 250мм."""
    # scale подобран так, чтобы 1% = 10мм → 15% = 150мм (<200 → 250)
    facades = [{"zone": "lower", "bbox_w_pct": 15}]  # 15% от total
    result = calculate_scaled_facades(facades, total_width_mm=100)  # scale=100/15≈6.67
    # 15 × 6.67 = 100мм < 200 → 250
    assert result[0].width_mm == 250


def test_too_wide_corrected_to_1000():
    """Ширина >1200мм → корректируется до 1000мм."""
    facades = [{"zone": "lower", "bbox_w_pct": 50}]
    result = calculate_scaled_facades(facades, total_width_mm=3000)  # scale=60 → 3000мм
    assert result[0].width_mm == 1000


def test_boundary_widths_not_corrected():
    """Ровно 200 и ровно 1200 НЕ корректируются (строгие < и >).

    Сумма bbox_w = 2+12 = 14%. Подбираем total так, чтобы scale=100мм/%:
    total = 100 × 14 = 1400. Тогда 2×100=200мм (граница), 12×100=1200мм (граница).
    """
    facades = [
        {"zone": "lower", "bbox_w_pct": 2},   # → 200мм (не < 200)
        {"zone": "lower", "bbox_w_pct": 12},  # → 1200мм (не > 1200)
    ]
    result = calculate_scaled_facades(facades, total_width_mm=1400)
    assert result[0].width_mm == 200
    assert result[1].width_mm == 1200


# ════════════════════════════════════════════════════════════════════
# ВЫСОТЫ ПО ЗОНАМ
# ════════════════════════════════════════════════════════════════════

def test_default_heights_by_zone():
    """Высоты по умолчанию: lower=716, upper=596, penal=2500."""
    facades = [
        {"zone": "lower", "bbox_w_pct": 30},
        {"zone": "upper", "bbox_w_pct": 30},
        {"zone": "penal", "bbox_w_pct": 30},
    ]
    result = calculate_scaled_facades(facades, total_width_mm=3000)
    assert result[0].height_mm == 716
    assert result[1].height_mm == 596
    assert result[2].height_mm == 2500


def test_explicit_heights_override_defaults():
    """Явно переданные высоты переопределяют дефолтные."""
    facades = [
        {"zone": "lower", "bbox_w_pct": 30},
        {"zone": "upper", "bbox_w_pct": 30},
        {"zone": "penal", "bbox_w_pct": 30},
    ]
    result = calculate_scaled_facades(
        facades, total_width_mm=3000,
        lower_height_mm=820, upper_height_mm=720, penal_height_mm=2100,
    )
    assert result[0].height_mm == 820
    assert result[1].height_mm == 720
    assert result[2].height_mm == 2100


def test_unknown_zone_gets_lower_height():
    """Неизвестная зона → высота из heights.get(zone, 716) = 716 (дефолт lower)."""
    facades = [{"zone": "wardrobe", "bbox_w_pct": 30}]
    result = calculate_scaled_facades(facades, total_width_mm=2000)
    assert result[0].height_mm == 716


# ════════════════════════════════════════════════════════════════════
# ДЕФОЛТЫ ДЛЯ ОТСУТСТВУЮЩИХ КЛЮЧЕЙ
# ════════════════════════════════════════════════════════════════════

def test_missing_bbox_w_in_sum_treated_as_zero():
    """Отсутствует bbox_w_pct при подсчёте суммы → считается как 0.

    ВАЖНО: в подсчёте total_pct (стр.68) используется f.get("bbox_w_pct", 0),
    а дефолт 20 применяется только в цикле чтения (стр.87).
    Поэтому один фасад без bbox_w_pct → total_pct=0 → масштаб невозможен → [].
    Это тонкое различие в дефолтах зафиксировано как регрессионный тест.
    """
    facades = [{"zone": "lower"}]  # нет bbox_w_pct
    result = calculate_scaled_facades(facades, total_width_mm=1000)
    assert result == []


def test_missing_bbox_w_but_with_other_present():
    """Один фасад без bbox_w_pct + один с bbox_w_pct.

    total_pct = 0 + 30 = 30% (только второй вносит вклад).
    scale = 900/30 = 30 мм/%.
    Первый (без ключа): width = 20 × 30 = 600мм (дефолт 20 в цикле чтения).
    Второй: width = 30 × 30 = 900мм.
    """
    facades = [
        {"zone": "lower"},              # bbox_w_pct отсутствует
        {"zone": "lower", "bbox_w_pct": 30},
    ]
    result = calculate_scaled_facades(facades, total_width_mm=900)
    assert len(result) == 2
    assert result[0].width_mm == 600   # 20 (дефолт чтения) × 30
    assert result[1].width_mm == 900


def test_missing_zone_defaults_to_lower():
    """Отсутствует zone → 'lower'."""
    facades = [{"bbox_w_pct": 20}]
    result = calculate_scaled_facades(facades, total_width_mm=1000)
    assert result[0].zone == "lower"
    assert result[0].height_mm == DEFAULT_HEIGHTS["lower"]


def test_missing_bbox_x_defaults_to_zero():
    """Отсутствует bbox_x_pct → 0."""
    facades = [{"zone": "lower", "bbox_w_pct": 20}]
    result = calculate_scaled_facades(facades, total_width_mm=1000)
    assert result[0].bbox_x_pct == 0


# ════════════════════════════════════════════════════════════════════
# ТИП ВОЗВРАЩАЕМОГО ЗНАЧЕНИЯ
# ════════════════════════════════════════════════════════════════════

def test_returns_scaled_facade_instances():
    """Возвращаемые элементы — экземпляры ScaledFacade."""
    facades = [{"zone": "lower", "bbox_w_pct": 30}]
    result = calculate_scaled_facades(facades, total_width_mm=1000)
    assert isinstance(result[0], ScaledFacade)
    assert isinstance(result[0].width_mm, int)
    assert isinstance(result[0].height_mm, int)


def test_preserves_bbox_pct_in_result():
    """bbox_x_pct и bbox_w_pct сохраняются в результате для отладки."""
    facades = [{"zone": "lower", "bbox_x_pct": 5, "bbox_w_pct": 30}]
    result = calculate_scaled_facades(facades, total_width_mm=1000)
    assert result[0].bbox_x_pct == 5
    assert result[0].bbox_w_pct == 30


# ════════════════════════════════════════════════════════════════════
# ПРОБРОС is_corner (после фикса B2)
# ════════════════════════════════════════════════════════════════════

def test_is_corner_propagated_from_dict():
    """is_corner=True в исходном dict → сохраняется в ScaledFacade."""
    facades = [{"zone": "lower", "bbox_w_pct": 20, "is_corner": True}]
    result = calculate_scaled_facades(facades, total_width_mm=1000)
    assert result[0].is_corner is True


def test_is_corner_defaults_false_when_absent():
    """Отсутствует is_corner → дефолт False."""
    facades = [{"zone": "lower", "bbox_w_pct": 20}]
    result = calculate_scaled_facades(facades, total_width_mm=1000)
    assert result[0].is_corner is False


def test_is_corner_explicit_false():
    """is_corner=False в dict → False."""
    facades = [{"zone": "lower", "bbox_w_pct": 20, "is_corner": False}]
    result = calculate_scaled_facades(facades, total_width_mm=1000)
    assert result[0].is_corner is False
