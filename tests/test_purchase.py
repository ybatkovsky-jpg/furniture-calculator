"""Этап 4: закупочный список из раскроя и кромки."""

from __future__ import annotations

from app.domain.nesting import nest_parts
from app.domain.parts import Part
from app.domain.purchase import build_purchase_list


def test_purchase_list_has_sheets_and_edge() -> None:
    part = Part(
        id="m1:side",
        module_id="m1",
        name="Боковина",
        material="ЛДСП",
        width_mm=600,
        height_mm=800,
        thickness_mm=16,
        quantity=2,
    )
    nesting = nest_parts([part])
    purchase = build_purchase_list(
        nesting,
        {"0.8": 12.5, "0.4": 30.0},
        prices={"Лист ЛДСП": 100.0, "Кромка 0.8 мм": 50.0},
        hdf_sheets=1,
        facades_area_m2=2.5,
    )
    names = [line.name for line in purchase.lines]

    assert "Лист ЛДСП" in names
    assert "Лист ХДФ" in names
    assert "Фасады" in names
    assert "Кромка 0.8 мм" in names
    assert "Кромка 0.4 мм" in names
    assert all(line.quantity > 0 for line in purchase.lines)
    sheet = next(line for line in purchase.lines if line.name == "Лист ЛДСП")
    assert sheet.unit_price == 100.0
    assert sheet.total == sheet.quantity * 100.0
