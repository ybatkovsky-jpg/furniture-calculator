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
