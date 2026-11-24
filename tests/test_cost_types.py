"""Этап 6: классификация затрат и статус финансовой политики."""

from __future__ import annotations

from app.domain.cost_lines import CostLine, cost_type_totals
from app.services.calc_engine import calculate_full_cost
from app.services.calculation_persist import quote_from_result
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


def test_cost_type_totals_match_cost_price() -> None:
    result = calculate_full_cost(
        [
            {
                "type": "lower_base",
                "width": 600,
                "depth": 560,
                "height": 820,
                "quantity": 1,
                "shelves": 1,
                "facades": {"count": 1},
            }
        ],
        {
            "ldsp": {"price": 5100, "price_unit": "лист"},
            "facades": {"name": "ФАСАДЫ ПВХ", "price": 6000, "price_unit": "м²"},
            "edge": {"price": 34, "thickness_mm": 0.8},
        },
        {
            "hinges": {"price": 172},
            "drawers": {"price": 5300},
            "handles": {"price": 550},
        },
        [],
        zone_type="Гостиная",
        price_book={"hdf": 1380, "confirmat": 2, "adjustable_feet": 50, "plinth": 1500},
    )
    totals = cost_type_totals(result.lines)

    assert abs(sum(totals.values()) - result.cost_breakdown.cost_price) <= 0.01
    assert all(value >= 0 for value in totals.values())

    quote = quote_from_result(result)
    assert quote["policy_status"] == "approved"
    assert quote["cost_type_totals"] == totals
    assert all("cost_type" in line for line in quote["lines"])
