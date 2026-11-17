"""P1-08: бренд и формат ЛДСП не зависят от выбранной кромки.

До исправления кромка EGGER делала бренд «EGGER» для ЛДСП SLOTEX: лист
5,03 м² вместо 3,6 м², число листов менялось от выбора кромки (67 132 против
51 039 ₽ на аудиторском сценарии).
"""

from app.services.calc_engine import calculate_full_cost
from app.services.image_analyzer import RecognizedModule
from app.services.quantity_calc import (
    SHEET_LDSP_BY_BRAND,
    SHEET_LDSP_SLOTEX_LONG,
    calculate_quantities,
)


def _module():
    return RecognizedModule(
        type="penal", width=900, depth=560, height=2100,
        facades={"count": 2, "type": "doors"},
    )


def _quantities(material_names, ldsp_name):
    return calculate_quantities(
        [_module()], "", material_names, ldsp_name=ldsp_name, zone_type="Гостиная"
    )


def test_edge_brand_does_not_change_ldsp_brand_or_sheets():
    materials_edge_egger = [
        "SLOTEX белый", "Кромка EGGER 0,8*19", "ФАСАДЫ ПВХ 16мм",
    ]
    materials_edge_lamarty = [
        "SLOTEX белый", "Кромка LAMARTY 0,8*19", "ФАСАДЫ ПВХ 16мм",
    ]
    first = _quantities(materials_edge_egger, "SLOTEX белый")
    second = _quantities(materials_edge_lamarty, "SLOTEX белый")

    assert first.ldsp_brand == "SLOTEX"
    assert second.ldsp_brand == "SLOTEX"
    assert first.ldsp_sheets == second.ldsp_sheets
    assert first.ldsp_area_m2 == second.ldsp_area_m2


def test_slotex_uses_its_own_sheet_format():
    quantities = _quantities(["SLOTEX белый"], "SLOTEX белый")
    assert SHEET_LDSP_BY_BRAND["SLOTEX"]["area_m2"] == 3.6
    # 900×560×2100 пенал: площадь/3,6 листа, а не /5,796.
    assert quantities.ldsp_sheets >= 1
    assert quantities.ldsp_area_m2 > 0
    assert SHEET_LDSP_SLOTEX_LONG["area_m2"] == 2.52


def test_slotex_sheets_differ_from_default_format():
    """Тот же корпус: SLOTEX (3,6 м²) и неизвестный бренд (5,796 м²) — разное."""
    slotex = _quantities(["SLOTEX белый"], "SLOTEX белый")
    unknown = _quantities(["Неизвестный материал"], "Неизвестный материал")
    assert slotex.ldsp_area_m2 == unknown.ldsp_area_m2  # площадь деталей та же
    assert slotex.ldsp_sheets >= unknown.ldsp_sheets     # листов не меньше
    assert any("по умолчанию" in w for w in unknown.warnings)


def test_full_cost_ignores_edge_for_sheet_count():
    base = {
        "ldsp": {"name": "SLOTEX белый", "price": 5600, "price_unit": "лист"},
        "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000, "price_unit": "м²"},
    }
    edges = {
        "egger": [{"name": "Кромка EGGER 0,8*19", "thickness_mm": 0.8, "price": 34}],
        "lamarty": [{"name": "Кромка LAMARTY 0,8*19", "thickness_mm": 0.8, "price": 40}],
    }
    results = []
    for edge in edges.values():
        materials = dict(base, edge=edge)
        results.append(calculate_full_cost(
            [{
                "type": "penal", "width": 900, "depth": 560, "height": 2100,
                "quantity": 1, "facades": {"count": 2, "type": "doors"},
            }],
            materials,
            {"hinges": {"price": 200, "brand": "FIRMAX"}, "handles": {"price": 500}},
            [],
            zone_type="Гостиная",
        ))
    assert results[0].sheets_calc.total_sheets == results[1].sheets_calc.total_sheets
