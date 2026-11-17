"""P2-14: нулевые и отрицательные размеры — ошибка, а не смета на 36 000 ₽."""

import pytest

from app.services.calc_engine import calculate_full_cost
from app.services.image_analyzer import RecognizedModule
from app.services.quantity_calc import calculate_quantities


def _module(**overrides):
    data = dict(type="lower_base", width=0, depth=0, height=0, quantity=1)
    data.update(overrides)
    return data


def test_zero_module_is_rejected_by_quantity_calc():
    module = RecognizedModule(type="lower_base", width=0, depth=0, height=0)
    with pytest.raises(ValueError, match="больше нуля"):
        calculate_quantities([module])


def test_zero_module_is_rejected_by_full_cost():
    """0×0×0 раньше давал 1 лист и смету ~36 000 ₽."""
    with pytest.raises(ValueError, match="больше нуля"):
        calculate_full_cost([_module()], {}, {}, [])


def test_negative_size_is_rejected():
    with pytest.raises(ValueError, match="больше нуля"):
        calculate_full_cost([_module(width=-600, height=820)], {}, {}, [])


def test_negative_depth_is_rejected():
    with pytest.raises(ValueError, match="отрицательной"):
        calculate_quantities(
            [RecognizedModule(type="lower_base", width=600, depth=-560, height=820)]
        )


def test_missing_depth_is_allowed():
    """Глубина 0 может быть не прочитана: не блокируем, но модуль считается."""
    quantities = calculate_quantities(
        [RecognizedModule(type="lower_base", width=600, depth=0, height=820)]
    )
    assert quantities.ldsp_sheets >= 1
