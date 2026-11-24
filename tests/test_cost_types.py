"""Этап 6: классификация затрат и статус финансовой политики."""

from __future__ import annotations

from app.domain.cost_lines import CostLine, cost_type_totals
from app.services.pricing_policy import PricingPolicy


def _line(kind: str, amount: float) -> CostLine:
    return CostLine(
        kind=kind,
        name=kind,
        quantity=1,
        unit="шт",
        unit_price=amount,
        amount=amount,
        price_source="selected",
    )


def test_cost_types_group_materials_and_hardware() -> None:
    lines = [_line("ldsp", 100), _line("edge", 50), _line("hinge", 30)]
    totals = cost_type_totals(lines)

    assert totals == {"hardware": 30, "material": 150}
    assert sum(totals.values()) == sum(line.amount for line in lines)


def test_unknown_kind_goes_to_other() -> None:
    assert _line("custom", 1).cost_type == "other"


def test_policy_status_is_approved() -> None:
    policy = PricingPolicy()

    assert policy.policy_status == "approved"
    assert policy.as_dict()["policy_status"] == "approved"
