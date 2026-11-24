"""Допуск точности КП ±10 %."""

from __future__ import annotations

import pytest

from app.domain.accuracy import compare_kp, relative_error, within_kp_tolerance


def test_relative_error_and_tolerance() -> None:
    assert relative_error(110, 100) == pytest.approx(0.10)
    assert within_kp_tolerance(110, 100) is True
    assert within_kp_tolerance(111, 100) is False
    assert within_kp_tolerance(90, 100) is True


def test_zero_expected_is_data_error() -> None:
    with pytest.raises(ValueError):
        relative_error(100, 0)


def test_compare_kp_reports_worst_row() -> None:
    report = compare_kp(
        {"final_cash": 110, "cost_price": 80},
        {"final_cash": 100, "cost_price": 100},
    )

    assert report["ok"] is False
    assert report["max_error"] == pytest.approx(0.20)
    assert report["rows"][0]["ok"] is True
    assert report["rows"][1]["ok"] is False


def test_compare_kp_missing_value_is_failure() -> None:
    report = compare_kp({"final_cash": 100}, {"final_cash": 100, "cost_price": 50})

    assert report["ok"] is False
    assert report["rows"][1]["missing"] is True
