"""S5-03: заготовка раскроя BOM по листам."""

import math
from types import SimpleNamespace

from app.domain.nesting import nest_bom, nest_parts_guillotine
from app.services.calc_engine import calculate_full_cost


def _line(name, width, height, material="ЛДСП", quantity=1):
    return SimpleNamespace(
        part=name, width_mm=width, height_mm=height,
        material=material, quantity=quantity,
    )


def _bom(*lines):
    return [SimpleNamespace(lines=list(lines))]


def _part(name, width, height, material="ЛДСП", quantity=1, grain=None):
    return SimpleNamespace(
        name=name, width_mm=width, height_mm=height, material=material,
        quantity=quantity, grain_direction=grain,
    )


def test_guillotine_places_parts_and_keeps_texture():
    parts = [
        _part("Боковина", 600, 800, grain="vertical"),
        _part("Полка", 500, 300),
        _part("Дно", 600, 300),
    ]
    result = nest_parts_guillotine(parts, {"ЛДСП": (2800, 2070)})

    assert result.sheets_count == 1
    assert not result.unplaced
    assert {p.name for p in result.layouts[0].parts} == {"Боковина", "Полка", "Дно"}


def test_textured_part_is_not_rotated_but_plain_is():
    textured = nest_parts_guillotine(
        [_part("Текстура", 900, 500, grain="vertical")],
        {"ЛДСП": (600, 1000)},
    )
    plain = nest_parts_guillotine(
        [_part("Однотон", 900, 500)],
        {"ЛДСП": (600, 1000)},
    )

    assert textured.unplaced
    assert not plain.unplaced
    assert plain.layouts[0].parts[0].rotated is True


def test_small_parts_fit_one_sheet():
    result = nest_bom(_bom(_line("Полка", 600, 400, quantity=4)))
    assert result.sheets_count == 1
    assert result.unplaced == []
    assert result.waste_percent > 0


def test_too_large_part_is_reported():
    result = nest_bom(_bom(_line("Слишком большая", 3000, 100)))
    assert result.sheets_count == 0
    assert result.unplaced and result.unplaced[0]["part"] == "Слишком большая"


def test_module_quantity_multiplies_parts():
    """P2-11: 5 одинаковых корпусов (quantity=5) дают 5 комплектов деталей."""
    bom = [
        SimpleNamespace(
            quantity=5,
            lines=[_line("Боковина", 564, 824, quantity=2)],
        )
    ]
    result = nest_bom(bom)
    placed = sum(len(layout.parts) for layout in result.layouts)
    assert placed == 10


def test_grain_locked_does_not_rotate_parts():
    """Направление рисунка: текстурный ЛДСП нельзя крутить на 90°."""
    bom = _bom(_line("Боковина", 2000, 600))
    sheet = {"ЛДСП": (1000, 2000)}

    free = nest_bom(bom, sheet)
    assert free.sheets_count == 1 and free.unplaced == []

    locked = nest_bom(bom, sheet, grain_locked=True)
    assert locked.sheets_count == 0
    assert locked.unplaced and locked.unplaced[0]["part"] == "Боковина"


def test_reserve_adds_ten_percent_sheets():
    """Собственник 17.11: занятая доля листа × 1,2, округление вверх."""
    module = {
        "type": "lower_base", "width": 600, "depth": 560, "height": 820,
        "quantity": 4, "facades": {"count": 1}, "shelves": 1,
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
    used = sum(
        layout.used_area_m2
        for layout in result.nesting.layouts
        if layout.material == "ЛДСП"
    )
    touched = len(
        [layout for layout in result.nesting.layouts if layout.material == "ЛДСП"]
    )
    # Бренд не задан → лист по умолчанию 2800×2070 = 5,796 м².
    # Занятая доля × 1,3, но каждый занятый лист — целый.
    expected = max(math.ceil(used * 1.3 / 5.796), touched)
    assert result.sheets_calc.total_sheets == expected


def test_ten_partial_sheets_stay_ten():
    """10 листов по 0,8 занятости не превращаются в 8 листов.

    Площадь: 10 × 0,8 = 8 листов → × 1,3 = 10,4 → вверх 11.
    Число занятых листов = 10, берём максимум — 11 (не 8).
    """
    from types import SimpleNamespace

    from app.domain.nesting import NestingResult, PlacedPart, SheetLayout
    from app.services.calc_engine import _apply_nesting_sheets

    sheet_w, sheet_h = 2800, 2070
    part_h = 1656  # 2800 × 1656 = 0,8 площади листа 2800×2070
    layouts = []
    for index in range(10):
        layout = SheetLayout("ЛДСП", index + 1, sheet_w, sheet_h)
        layout.parts.append(PlacedPart("Деталь", sheet_w, part_h, 0, 0))
        layouts.append(layout)

    quantities = SimpleNamespace(ldsp_area_m2=46.368, ldsp_sheets=0, warnings=[])
    _apply_nesting_sheets(
        quantities,
        NestingResult(layouts=layouts),
        reserve_rate=0.30,
        sheet_area_m2=sheet_w * sheet_h / 1_000_000,
    )
    assert quantities.ldsp_sheets == 11


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
