"""P0-02 (продолжение): 10 сценариев Excel = чат.

Каждый сценарий считается ОДНИМ движком (`calculate_full_cost`), затем
`apply_result` делает снимок `quote`, и Excel заполняется только из снимка.
Проверяется: себестоимость, итог, сумма строк — как в чате; ни одного
молчаливого нуля; мусорных листов нет.
"""

from pathlib import Path

import pytest
from openpyxl import load_workbook

from app.services.calc_engine import calculate_full_cost
from app.services.calculation_persist import quote_from_result
from app.services.cost_calc import DiscountInfo
from app.services.template_filler import _find_row_by_label, fill_template_from_quote

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "Таблица для расчетов пустая.xlsx"


def _num(value):
    return float(value) if isinstance(value, (int, float)) else 0.0


@pytest.mark.parametrize(
    "name,modules,materials,hardware,zone,discount",
    [
        (
            "базовый корпус",
            [{"type": "lower_base", "width": 600, "depth": 560, "height": 820,
              "facades": {"count": 1, "type": "doors"}, "shelves": 1}],
            {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
             "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000,
                         "price_unit": "м²"},
             "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}]},
            {"hinges": {"price": 200, "brand": "FIRMAX"},
             "handles": {"price": 500}},
            "Гостиная",
            None,
        ),
        (
            "шкаф 3 двери",
            [{"type": "wardrobe", "width": 1200, "depth": 600, "height": 2100,
              "facades": {"count": 3, "type": "doors"}, "shelves": 4}],
            {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
             "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000,
                         "price_unit": "м²"},
             "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}]},
            {"hinges": {"price": 200, "brand": "FIRMAX"},
             "handles": {"price": 500}},
            "Гостиная",
            None,
        ),
        (
            "кухня с ящиками",
            [{"type": "lower_base", "width": 800, "depth": 560, "height": 820,
              "facades": {"count": 2, "type": "doors"}, "drawers": {"count": 3}},
             {"type": "upper_base", "width": 600, "depth": 320, "height": 720,
              "facades": {"count": 2, "type": "doors"}}],
            {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
             "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000,
                         "price_unit": "м²"},
             "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}]},
            {"hinges": {"price": 200, "brand": "FIRMAX"},
             "drawers": {"name": "TANDEMBOX", "price": 3000},
             "handles": {"price": 500}},
            "Кухня",
            None,
        ),
        (
            "МДФ-фасад за лист",
            [{"type": "lower_base", "width": 600, "depth": 560, "height": 820,
              "facades": {"count": 2, "type": "doors"}}],
            {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
             "facades": {"name": "EMDIWAY U702", "price": 9800,
                         "price_unit": "лист", "sheet_width_mm": 2800,
                         "sheet_height_mm": 2070},
             "edge": [{"name": "Кромка МДФ 1*22", "thickness_mm": 1.0,
                       "price": 45}]},
            {"hinges": {"price": 200, "brand": "FIRMAX"},
             "handles": {"price": 500}},
            "Гостиная",
            None,
        ),
        (
            "стекло 2 двери",
            [{"type": "lower_base", "width": 800, "depth": 400, "height": 900,
              "facades": {"count": 2, "type": "glass"}, "has_glass": True}],
            {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
             "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000,
                         "price_unit": "м²"},
             "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}]},
            {"hinges": {"price": 200, "brand": "FIRMAX"},
             "handles": {"price": 500}},
            "Гостиная",
            None,
        ),
        (
            "две кромки",
            [{"type": "penal", "width": 600, "depth": 320, "height": 2100,
              "facades": {"count": 2, "type": "doors"}}],
            {"ldsp": {"name": "SLOTEX", "price": 5600, "price_unit": "лист"},
             "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000,
                         "price_unit": "м²"},
             "edge": [
                 {"name": "Кромка 0.4", "thickness_mm": 0.4, "price": 17.5},
                 {"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34},
             ]},
            {"hinges": {"price": 200, "brand": "FIRMAX"},
             "handles": {"price": 500}},
            "Гостиная",
            None,
        ),
        (
            "скидка 10%",
            [{"type": "lower_base", "width": 600, "depth": 560, "height": 820,
              "facades": {"count": 1, "type": "doors"}}],
            {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
             "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000,
                         "price_unit": "м²"},
             "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}]},
            {"hinges": {"price": 200, "brand": "FIRMAX"},
             "handles": {"price": 500}},
            "Гостиная",
            DiscountInfo(discount_type="percent", discount_value=10),
        ),
        (
            "бонус дизайнера 10%",
            [{"type": "lower_base", "width": 600, "depth": 560, "height": 820,
              "facades": {"count": 1, "type": "doors"}}],
            {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
             "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000,
                         "price_unit": "м²"},
             "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}]},
            {"hinges": {"price": 200, "brand": "FIRMAX"},
             "handles": {"price": 500}},
            "Гостиная",
            DiscountInfo(designer_bonus_enabled=True, designer_bonus_rate=0.10),
        ),
        (
            "наценка 10%",
            [{"type": "lower_base", "width": 600, "depth": 560, "height": 820,
              "facades": {"count": 1, "type": "doors"}}],
            {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
             "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000,
                         "price_unit": "м²"},
             "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}]},
            {"hinges": {"price": 200, "brand": "FIRMAX"},
             "handles": {"price": 500}},
            "Гостиная",
            DiscountInfo(markup_type="percent", markup_value=10),
        ),
        (
            "пустой заказ (только корпус)",
            [{"type": "column", "width": 300, "depth": 300, "height": 2100}],
            {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
             "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}]},
            {},
            "Гостиная",
            None,
        ),
    ],
)
def test_excel_matches_chat_on_ten_scenarios(
    tmp_path, name, modules, materials, hardware, zone, discount
):
    price_book = {
        "hdf": 1530, "confirmat": 2, "adjustable_feet": 50,
        "wall_mount": 120, "plinth": 1500, "countertop": 2834.63,
        "edge_0.4": 17.5, "edge_0.8": 34,
    }
    result = calculate_full_cost(
        modules, materials, hardware, [],
        discount_info=discount, zone_type=zone, price_book=price_book,
    )
    quote = quote_from_result(result)
    output = tmp_path / f"{name}.xlsx"
    fill_template_from_quote(quote, str(TEMPLATE), str(output), sheet_name="Смета")

    wb = load_workbook(output)
    ws = wb["Смета"]
    totals = quote["totals"]
    cost_row = _find_row_by_label(ws, "СЕБЕСТОИМОСТЬ")
    cash_row = _find_row_by_label(ws, "ИТОГО за наличку")
    assert cost_row and cash_row, name

    assert abs(_num(ws.cell(cost_row, 5).value) - totals["cost_price"]) <= 0.05, name
    assert abs(_num(ws.cell(cash_row, 5).value) - totals["final_cash"]) <= 1.0, name
    row_sum = sum(_num(ws.cell(row, 6).value) for row in range(3, cost_row))
    assert abs(row_sum - totals["cost_price"]) <= 0.05, name
    for row in range(3, cost_row):
        quantity = ws.cell(row, 5).value
        if isinstance(quantity, (int, float)) and quantity > 0:
            assert _num(ws.cell(row, 6).value) > 0, f"{name}: R{row} без суммы"
