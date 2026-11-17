"""P1-06: AI-модуль с ящиками доходит до выбора системы и получает цену.

Распознавание хранит `drawers: {"count": N}`, ручной ввод — `filling="drawers"`.
Раньше проверялся только `filling`: для PDF/фото выбор системы не предлагался,
`selected_hardware["drawers"]` оставался пуст, строка «Ящики» получала
количество без цены и КП блокировался.
"""

import pytest

from app.services.calc_engine import _drawer_system_from_selection, calculate_full_cost
from app.services.quantity_calc import calculate_quantities, module_has_drawers


@pytest.mark.parametrize("module", [
    {"drawers": {"count": 3}},
    {"drawers": [{"type": "standard"}, {"type": "standard"}]},
    {"drawers": 2},
    {"drawer_counts": {"low": 2, "high": 1}},
    {"filling": "drawers", "quantity": 3},
])
def test_detects_drawers_in_all_schemas(module):
    assert module_has_drawers(module) is True


@pytest.mark.parametrize("module", [
    {"facades": {"count": 2}},
    {"drawers": {"count": 0}},
    {"filling": "doors"},
    {},
])
def test_no_false_drawers(module):
    assert module_has_drawers(module) is False


def test_selected_system_sets_quantity_system():
    module = {
        "type": "lower_base", "width": 450, "depth": 560, "height": 820,
        "facades": {"count": 1, "type": "doors"}, "drawers": {"count": 3},
    }
    from app.services.image_analyzer import RecognizedModule

    recognized = RecognizedModule(
        type="lower_base", width=450, depth=560, height=820,
        facades={"count": 1}, drawers={"count": 3},
    )
    for name, expected in [
        ("BLUM LEGRABOX 193 мм", "Legrabox"),
        ("Ящик TANDEMBOX 144 мм", "Tandembox"),
        ("BOYARD СТАРТ h=86", "Boyard Start"),
    ]:
        assert _drawer_system_from_selection({"drawers": {"name": name}}) == expected
        quantities = calculate_quantities(
            [recognized], "", [], zone_type="Кухня", drawer_system=expected
        )
        assert quantities.drawer_system == expected
    assert module  # модуль-схема использована в тестах выше


def test_ai_module_with_drawers_gets_price_and_kp():
    result = calculate_full_cost(
        [{
            "type": "lower_base", "width": 450, "depth": 560, "height": 820,
            "quantity": 1, "facades": {"count": 1, "type": "doors"},
            "drawers": {"count": 3},
        }],
        {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
         "facades": {"name": "ПВХ", "price": 10000, "price_unit": "м²"},
         "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}]},
        {"hinges": {"price": 200, "brand": "FIRMAX"},
         "drawers": {"name": "BLUM LEGRABOX", "price": 4000},
         "handles": {"price": 500}},
        [],
        zone_type="Кухня",
        price_book={
            "hdf": 1530, "confirmat": 2, "adjustable_feet": 50,
            "wall_mount": 120, "plinth": 1500, "countertop": 2834.63,
            "edge_0.4": 17.5, "edge_0.8": 34,
        },
    )
    assert result.hardware_calc.drawers_total == 3
    drawers = [line for line in result.lines if line.kind == "drawer"]
    assert drawers and drawers[0].amount == 12000
    assert result.can_issue_kp is True
