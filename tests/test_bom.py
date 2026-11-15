"""S5-01: деталировка модуля до отдельной детали."""

from app.domain.bom import build_bom, build_module_bom
from app.services.calc_engine import calculate_full_cost


def _module():
    return {
        "type": "lower_base", "width": 600, "depth": 560, "height": 820,
        "quantity": 1, "shelves": 1,
        "facades": {"count": 2, "type": "doors"},
        "drawers": {"count": 2},
    }


def test_bom_parts_and_sizes():
    bom = build_module_bom(_module())
    by_part = {line.part: line for line in bom.lines}

    assert by_part["Боковина"].quantity == 2
    assert (by_part["Боковина"].width_mm, by_part["Боковина"].height_mm) == (564, 824)
    assert by_part["Дно"].width_mm == 604
    assert by_part["Полка"].quantity == 1
    assert by_part["Задняя стенка"].material == "ХДФ"
    assert (by_part["Задняя стенка"].width_mm, by_part["Задняя стенка"].height_mm) == (600, 820)
    assert by_part["Фасад 1"].width_mm == 297
    assert by_part["Фасад 1"].height_mm == 816
    assert by_part["Фасад ящика 1"].quantity == 1


def test_bom_is_saved_in_quote():
    result = calculate_full_cost(
        [_module()],
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
        price_book={
            "hdf": 1380, "confirmat": 2, "adjustable_feet": 50,
            "plinth": 1500, "edge_0.4": 17.5,
        },
    )
    assert result.bom
    assert result.bom[0].lines
