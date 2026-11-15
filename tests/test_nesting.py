"""S5-03: заготовка раскроя BOM по листам."""

from types import SimpleNamespace

from app.domain.nesting import SheetLayout, nest_bom
from app.services.calc_engine import calculate_full_cost


def _line(name, width, height, material="ЛДСП", quantity=1):
    return SimpleNamespace(
        part=name, width_mm=width, height_mm=height,
        material=material, quantity=quantity,
    )


def _bom(*lines):
    return [SimpleNamespace(lines=list(lines))]


def test_small_parts_fit_one_sheet():
    result = nest_bom(_bom(_line("Полка", 600, 400, quantity=4)))
    assert result.sheets_count == 1
    assert result.unplaced == []
    assert result.waste_percent > 0


def test_too_large_part_is_reported():
    result = nest_bom(_bom(_line("Слишком большая", 3000, 100)))
    assert result.sheets_count == 0
    assert result.unplaced and result.unplaced[0]["part"] == "Слишком большая"


def test_calculation_stores_nesting():
    module = {
        "type": "lower_base", "width": 600, "depth": 560, "height": 820,
        "quantity": 1, "facades": {"count": 1}, "shelves": 1,
    }
    result = calculate_full_cost(
        [module],
        {
            "ldsp": {"price": 5100, "price_unit": "лист"},
            "facades": {"name": "ФАСАДЫ ПВХ", "price": 6000, "price_unit": "м²"},
            "edge": {"price": 34, "thickness_mm": 0.8},
        },
        {"hinges": {"price": 172}, "handles": {"price": 550}},
        [],
        zone_type="Гостиная",
        price_book={
            "hdf": 1380, "confirmat": 2, "adjustable_feet": 50,
            "plinth": 1500, "edge_0.4": 17.5,
        },
    )
    assert result.nesting is not None
    assert result.nesting.sheets_count >= 1
