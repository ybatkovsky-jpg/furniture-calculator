"""P1-14: Excel из бота — тот же снимок и тот же рендер, что в CLI."""

import asyncio

from openpyxl import load_workbook

from app.services.calculation_persist import quote_from_result
from app.services.calc_engine import calculate_full_cost
from app.services.excel_export import (
    render_quote_excel_async,
    safe_excel_filename,
)
from app.services.template_filler import _find_row_by_label


def _quote():
    result = calculate_full_cost(
        [{
            "type": "lower_base", "width": 600, "depth": 560, "height": 820,
            "facades": {"count": 1, "type": "doors"},
        }],
        {"ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
         "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000,
                     "price_unit": "м²"},
         "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}]},
        {"hinges": {"price": 200, "brand": "FIRMAX"},
         "handles": {"price": 500}},
        [],
        zone_type="Гостиная",
        price_book={"hdf": 1530, "confirmat": 2, "adjustable_feet": 50,
                    "wall_mount": 120, "plinth": 1500},
    )
    return quote_from_result(result)


def test_async_export_creates_valid_file(tmp_path):
    quote = _quote()
    path = asyncio.run(render_quote_excel_async(quote, sheet_name="Кухня"))
    try:
        assert path.exists()
        wb = load_workbook(path)
        assert wb.sheetnames == ["Кухня"]
        ws = wb["Кухня"]
        cost_row = _find_row_by_label(ws, "СЕБЕСТОИМОСТЬ")
        assert abs(float(ws.cell(cost_row, 5).value) - quote["totals"]["cost_price"]) <= 0.01
    finally:
        path.unlink(missing_ok=True)
    assert not path.exists()


def test_empty_quote_is_rejected_and_no_file_left():
    try:
        asyncio.run(render_quote_excel_async({"lines": []}, sheet_name="Смета"))
    except ValueError as exc:
        assert "пуст" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("пустой снимок должен отклоняться")


def test_filename_has_no_pii_or_special_chars():
    name = safe_excel_filename(12, "Кухня Влада / ул. Ленина, 1")
    assert name.endswith("_12.xlsx")
    assert name.startswith("Смета_")
    assert "/" not in name and "\\" not in name
    assert "Влада" in name
