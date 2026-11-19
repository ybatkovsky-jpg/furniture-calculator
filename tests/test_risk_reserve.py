"""Запас на возможные ошибки (решение 18.11).

По умолчанию 0 — расчёт не меняется. При включении запас входит в ИТОГО
до прибыли, отдельной строкой `risk_reserve`.
"""

from __future__ import annotations

from app.services.cost_calc import calculate_cost

BASE = {
    "material_cost": 100_000,
    "edge_cost": 0,
    "facade_cost": 0,
    "hardware_cost": 0,
}


def test_risk_reserve_off_by_default() -> None:
    result = calculate_cost(**BASE)
    assert result.risk_reserve == 0


def test_risk_reserve_included_in_subtotal() -> None:
    base = calculate_cost(**BASE)
    with_reserve = calculate_cost(**BASE, risk_reserve_rate=0.05)
    assert with_reserve.risk_reserve == 5_000
    # Монтаж с 19.11 считается от базы, поэтому запас увеличивает и его.
    install_growth = with_reserve.installation - base.installation
    assert install_growth > 0
    assert with_reserve.subtotal == base.subtotal + 5_000 + install_growth
