"""P1-07: GOLA/push/lift — проект без ручек выпускает КП без тупика.

Раньше кнопка «Без ручек (Gola/push)» удаляла выбранную ручку, но
`handles_count` оставался > 0: строка «Ручки» получала количество без цены и
КП блокировалось навсегда. Теперь способ открывания — явный режим.
"""

import pytest

from app.services.calc_engine import calculate_full_cost
from app.services.image_analyzer import RecognizedModule
from app.services.quantity_calc import calculate_quantities


def _module(module_type="lower_base", height=820, doors=2):
    return RecognizedModule(
        type=module_type, width=600, depth=560, height=height,
        facades={"count": doors, "type": "doors"},
    )


def _q(mode, module=None):
    return calculate_quantities(
        [module or _module()], "", [], zone_type="Кухня", handle_mode=mode
    )


def test_handles_mode_keeps_old_behaviour():
    quantities = _q("handles")
    assert quantities.handles_count > 0
    assert quantities.gola_horizontal_pcs == 0
    assert quantities.push_open_count == 0
    assert quantities.lift_mechanism_count == 0


def test_gola_mode_removes_handles_and_adds_profile():
    quantities = _q("gola")
    assert quantities.handles_count == 0
    assert quantities.gola_horizontal_m > 0
    assert quantities.gola_horizontal_pcs >= 1


def test_push_mode_counts_mechanisms_per_hinge():
    quantities = _q("push")
    assert quantities.handles_count == 0
    assert quantities.hinges_count > 0
    assert quantities.push_open_count == quantities.hinges_count


def test_lift_mode_replaces_hinges_with_lifts():
    quantities = _q("lift", _module("upper_base", 720))
    assert quantities.handles_count == 0
    assert quantities.hinges_count == 0
    assert quantities.lift_mechanism_count == 1


def _materials():
    return {
        "ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
        "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000, "price_unit": "м²"},
        "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}],
    }


def _price_book(**extra):
    book = {
        "hdf": 1530, "confirmat": 2, "adjustable_feet": 50,
        "wall_mount": 120, "plinth": 1500,
        "gola_horizontal": 3550, "gola_vertical": 1835,
        "countertop": 2834.63, "edge_0.4": 17.5, "edge_0.8": 34,
    }
    book.update(extra)
    return book


def _calc(mode, price_book):
    return calculate_full_cost(
        [{"type": "lower_base", "width": 600, "depth": 560, "height": 820,
          "facades": {"count": 2, "type": "doors"}}],
        _materials(),
        {"handle_mode": mode, "hinges": {"price": 200, "brand": "FIRMAX"}},
        [],
        zone_type="Кухня",
        price_book=price_book,
    )


def test_gola_project_can_issue_kp_with_profile_price():
    result = _calc("gola", _price_book())
    assert result.hardware_calc.handles_total == 0
    gola_lines = [line for line in result.lines if line.kind == "gola"]
    assert gola_lines and sum(line.amount for line in gola_lines) > 0
    assert result.can_issue_kp is True


def test_gola_without_price_blocks_kp():
    result = _calc("gola", {"hdf": 1530})
    assert result.hardware_calc.handles_total == 0
    assert result.can_issue_kp is False
    assert any("GOLA" in item for item in result.missing_prices)


def test_push_without_mechanism_price_blocks_kp():
    result = _calc("push", _price_book())
    assert result.hardware_calc.handles_total == 0
    assert result.can_issue_kp is False
    assert any("Push" in item for item in result.missing_prices)


def test_push_with_price_is_priced():
    result = _calc("push", _price_book(push_hinge=500))
    push_lines = [line for line in result.lines if line.kind == "push_open"]
    assert push_lines and push_lines[0].amount > 0
    assert result.can_issue_kp is True


def test_lift_without_price_blocks_and_with_price_passes():
    blocked = _calc("lift", _price_book())
    assert blocked.can_issue_kp is False
    assert any("Подъёмник" in item for item in blocked.missing_prices)

    ok = _calc("lift", _price_book(lift_mechanism=1200))
    lift_lines = [line for line in ok.lines if line.kind == "lift"]
    assert lift_lines and lift_lines[0].amount == 1200
    assert ok.can_issue_kp is True


def test_invalid_mode_falls_back_to_handles():
    quantities = _q("что-то")
    assert quantities.handles_count > 0
