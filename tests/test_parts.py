"""Технологический BOM: детали, кромка по торцам, перегородки, ящики."""

from __future__ import annotations

from app.domain.bom import build_bom, build_module_bom
from app.domain.nesting import nest_parts
from app.domain.parts import Part, build_module_parts, edge_totals, parts_area_m2
from app.services.calc_engine import calculate_full_cost


def _module(**kwargs):
    base = {
        "id": "m1",
        "type": "lower_base",
        "width": 800,
        "depth": 560,
        "height": 820,
        "quantity": 1,
        "shelves": 2,
        "partitions": 1,
        "has_plinth": True,
        "facades": {"count": 2, "type": "doors"},
        "drawers": {"count": 2},
    }
    base.update(kwargs)
    return base


def test_module_has_carcass_partitions_plinth_and_drawers() -> None:
    parts = build_module_parts(_module(drawer_boxes=True))
    names = {part.name for part in parts}

    assert "Боковина" in names
    assert "Перегородка" in names
    assert "Цоколь" in names
    assert "Задняя стенка" in names
    assert "Полка" in names
    assert any(name.startswith("Боковина ящика") for name in names)
    assert any(name.startswith("Дно ящика") for name in names)
    assert all(part.width_mm > 0 and part.height_mm > 0 for part in parts)
    assert all(part.quantity > 0 for part in parts)


def test_edge_is_everywhere_visible_08() -> None:
    parts = build_module_parts(_module())
    facade = next(part for part in parts if part.name == "Фасад 1")
    back = next(part for part in parts if part.name == "Задняя стенка")
    side = next(part for part in parts if part.name == "Боковина")

    assert {edge.side for edge in facade.edge_operations} == {
        "front", "back", "left", "right"
    }
    assert all(edge.thickness_mm == 0.8 for edge in facade.edge_operations)
    assert back.edge_operations == []
    assert any(
        edge.side == "front" and edge.thickness_mm == 0.8
        for edge in side.edge_operations
    )
    assert any(
        edge.side == "back" and edge.thickness_mm == 0.4
        for edge in side.edge_operations
    )


def test_edge_totals_are_meters() -> None:
    totals = edge_totals(build_module_parts(_module()))

    assert totals["0.8"] > 0
    assert totals["0.4"] > 0


def test_table_has_no_back_or_shelves() -> None:
    parts = build_module_parts(
        _module(type="table", shelves=0, drawers={"count": 0}, facades={"count": 0})
    )
    names = {part.name for part in parts}

    assert "Столешница" in names
    assert "Боковина стола" in names
    assert "Задняя стенка" not in names
    assert "Полка" not in names


def test_parts_are_saved_in_module_bom() -> None:
    bom = build_module_bom(_module())

    assert bom.parts
    assert bom.lines
    assert any(part.name == "Перегородка" for part in bom.parts)


def test_glass_and_profile_have_no_edge() -> None:
    parts = build_module_parts(
        _module(glass={"count": 1}, handle_mode="gola", facades={"count": 0})
    )
    glass = next(part for part in parts if part.name == "Стекло")
    profile = next(part for part in parts if part.name == "Профиль GOLA")

    assert glass.edge_operations == []
    assert profile.edge_operations == []
    assert glass.material == "Стекло"
    assert profile.material == "Алюминий"


def test_parts_nest_and_edge_are_linked() -> None:
    parts = build_module_parts(_module())
    nesting = nest_parts(parts)

    assert nesting.sheets_count > 0
    assert nesting.used_area_m2 > 0
    assert parts_area_m2(parts) > 0
    assert edge_totals(parts)["0.8"] > 0


def test_textured_part_is_not_rotated() -> None:
    part = Part(
        id="m1:side",
        module_id="m1",
        name="Текстура",
        material="ЛДСП",
        width_mm=2000,
        height_mm=300,
        thickness_mm=16,
        grain_direction="vertical",
    )
    nesting = nest_parts([part], sheet_sizes={"ЛДСП": (1000, 2500)})

    assert nesting.sheets_count == 0
    assert nesting.unplaced


def test_calculation_keeps_part_nesting_and_edge() -> None:
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
            "hdf": 1380,
            "confirmat": 2,
            "adjustable_feet": 50,
            "plinth": 1500,
            "edge_0.4": 17.5,
        },
    )

    assert result.parts_nesting is not None
    assert result.parts_nesting.sheets_count > 0
    assert result.parts_area_m2 > 0
    assert result.parts_edge_totals.get("0.8", 0) > 0
    assert result.purchase is not None
    assert result.purchase.lines


def test_part_bom_pricing_flag_uses_parts(monkeypatch) -> None:
    monkeypatch.setenv("PART_BOM_PRICING", "1")
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
            "hdf": 1380,
            "confirmat": 2,
            "adjustable_feet": 50,
            "plinth": 1500,
            "edge_0.4": 17.5,
        },
    )

    assert result.parts_nesting is not None
    assert result.parts_nesting.sheets_count > 0
    assert result.sheets_calc.total_sheets >= result.parts_nesting.sheets_count


def test_parts_have_unique_ids_across_modules() -> None:
    first, second = build_bom([_module(), _module()])
    first_ids = {part.id for part in first.parts}
    second_ids = {part.id for part in second.parts}

    assert first_ids
    assert second_ids
    assert first_ids.isdisjoint(second_ids)
    assert first.module_id == "m1"
    assert second.module_id == "m2"
