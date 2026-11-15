"""P1-24: деньги считаются Decimal с HALF_UP, количества — вверх."""

from app.domain.money import money_float, quantity_float, rub_float
from app.services.cost_calc import _round_half_up


def test_money_half_up_avoids_bankers_rounding():
    assert money_float("2.675") == 2.68      # round(2.675) в float дал бы 2.67
    assert money_float("1.005") == 1.01
    assert money_float(10) == 10.0
    assert money_float(-2.675) == -2.68


def test_rub_half_up():
    assert rub_float(2.5) == 3
    assert rub_float(2.49) == 2
    assert _round_half_up(2.5) == 3


def test_quantity_rounds_up_not_down():
    assert quantity_float(1.0001, 3) == 1.001
    assert quantity_float(2.000, 3) == 2.0
