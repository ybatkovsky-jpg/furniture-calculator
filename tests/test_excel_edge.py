"""P0-06: одна позиция кромки МДФ — одна строка Excel."""

import json
from pathlib import Path

from app.services import template_filler
from app.services.quantity_calc import calculate_quantities

ROOT = Path(__file__).resolve().parents[1]


def _module():
    return type(
        "M",
        (),
        {
            "type": "penal",
            "width": 600,
            "depth": 560,
            "height": 1500,
            "quantity": 1,
            "facades": {"count": 1, "type": "doors"},
            "drawers": None,
            "shelves": 0,
            "has_glass": False,
            "is_corner": False,
        },
    )()


def test_mdf_edge_field_single_for_one_supplier():
    """EMDIWAY → только edge_mdf_1mm_emdiway, остальные строки пустые."""
    q = calculate_quantities([_module()], "", ["EMDIWAY U702"], zone_type="kitchen")
    assert q.edge_mdf_1mm_m > 0

    qty_map = template_filler._build_quantity_map(q, ["EMDIWAY U702"], "Кухня")
    edge_keys = {
        key: value
        for key, value in qty_map.items()
        if key.startswith("edge_mdf_1mm_")
    }
    assert edge_keys == {"edge_mdf_1mm_emdiway": q.edge_mdf_1mm_m}


def test_template_has_fastener_and_countertop_rows():
    """P1-18: столешница и крепёж имеют строки в шаблоне (в диапазоне SUM)."""
    from openpyxl import load_workbook

    ws = load_workbook(
        ROOT / "templates" / "Таблица для расчетов пустая.xlsx"
    )["Рассчет"]
    for keywords in (
        ["Столешница", "постформинг"],
        ["Конфирмат", "7×50"],
        ["Регулируемая опора"],
        ["Настенный подвес"],
    ):
        row = template_filler._find_row_for_material(ws, keywords)
        assert row is not None, keywords
        # Строка должна попасть в SUM(F3:F345) — иначе не войдёт в себестоимость
        assert 3 <= row <= 345, keywords


def test_db_price_matches_keywords_and_unit():
    from app.services.price_catalog import PriceCatalogItem, find_price

    prices = [
        PriceCatalogItem(1, "LAMARTY однотон", "ЛДСП", "LAMARTY", 5100, "лист", None),
        PriceCatalogItem(2, "EGGER 0,8*19", "Кромка", "EGGER", 34, "м", 0.8),
    ]
    assert find_price(prices, ["LAMARTY", "однотон"], "листов").item.price == 5100
    assert find_price(prices, ["EGGER 0,8*19"], "м.п.").item.price == 34
    # Несовместимая единица не должна подставляться.
    assert find_price(prices, ["EGGER 0,8*19"], "листов") is None


def test_unfilled_item_goes_to_proche_with_price():
    """P1-18: ненайденная строка с ценой в прайсе попадает в «ПРОЧЕЕ»."""
    from openpyxl import load_workbook
    from app.services.price_catalog import PriceCatalogItem
    from app.services.template_filler import _append_unfilled_rows, _find_row_by_label

    wb = load_workbook(ROOT / "templates" / "Таблица для расчетов пустая.xlsx")
    ws = wb["Рассчет"]
    prices = [
        PriceCatalogItem(1, "Столешница ЛДСП", "Столешницы", None, 3000, "м", None)
    ]
    items = [{
        "room": "Кухня",
        "material": "Столешница",
        "expected_value": "5 м",
        "field": "countertop_length_m",
        "keywords": ["Столешница"],
    }]
    added = _append_unfilled_rows(ws, items, prices)

    assert len(added) == 1 and items[0]["added_to_extra"] is True
    cost_row = _find_row_by_label(ws, "СЕБЕСТОИМОСТЬ")
    header_row = next(
        r for r in range(1, ws.max_row + 1)
        if str(ws.cell(r, 1).value or "").startswith("ПРОЧЕЕ")
    )
    item_row = header_row + 1
    # Новая строка вошла в себестоимость (значение, не формула — P2-16).
    assert ws.cell(cost_row, 5).value == 15_000
    assert ws.cell(item_row, 6).value == 15_000
    # И цена/количество записаны.
    assert ws.cell(item_row, 4).value == 3000
    assert ws.cell(item_row, 5).value == 5

    # Финансовая секция после вставки пересобирается и не ломается.
    from app.services.pricing_policy import PricingPolicy
    from app.services.template_filler import _apply_pricing_policy

    assert _apply_pricing_policy(ws, PricingPolicy.from_config()) is True
    # «ИЗГОТОВЛЕНИЕ» идёт сразу после «СЕБЕСТОИМОСТЬ» и считается от неё.
    assert ws.cell(cost_row + 1, 5).value == 12_000


def test_unfilled_without_price_stays_in_problems():
    from openpyxl import load_workbook
    from app.services.template_filler import _append_unfilled_rows

    wb = load_workbook(ROOT / "templates" / "Таблица для расчетов пустая.xlsx")
    ws = wb["Рассчет"]
    items = [{
        "room": "Кухня", "material": "Неизвестно",
        "expected_value": "1 шт", "keywords": ["неизвестно"],
    }]
    assert _append_unfilled_rows(ws, items, []) == []
    assert "added_to_extra" not in items[0]


def test_mapping_does_not_share_edge_mdf_field():
    """Шесть строк шаблона больше не используют одно поле."""
    data = json.loads((ROOT / "templates" / "row_mapping.json").read_text(encoding="utf-8"))
    fields = [
        item["field"]
        for item in data["mappings"]
        if item["field"].startswith("edge_mdf_1mm_")
    ]
    assert len(fields) == 6
    assert len(set(fields)) == 6
