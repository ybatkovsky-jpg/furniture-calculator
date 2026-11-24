"""Этап 6: паритет Telegram / Excel / Web по одному снимку quote."""

from __future__ import annotations

from app.domain.cost_lines import cost_type_totals
from app.services.calc_engine import calculate_full_cost
from app.services.calculation_persist import quote_from_result


def _result():
    return calculate_full_cost(
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


def test_quote_matches_result_for_all_interfaces() -> None:
    result = _result()
    quote = quote_from_result(result)

    assert quote["totals"]["final_cash"] == result.total_cash
    assert quote["totals"]["final_noncash"] == result.total_noncash
    assert quote["cost_price"] == result.cost_breakdown.cost_price
    assert quote["policy_status"] == "approved"


def test_quote_lines_and_cost_types_add_up() -> None:
    result = _result()
    quote = quote_from_result(result)

    line_sum = round(sum(line["amount"] for line in quote["lines"]), 2)
    type_sum = round(sum(cost_type_totals(result.lines).values()), 2)

    assert line_sum == round(quote["cost_price"], 2)
    assert type_sum == line_sum
