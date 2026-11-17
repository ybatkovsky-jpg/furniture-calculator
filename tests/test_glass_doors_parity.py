"""P0-03: стекло считается по каждой двери и не оплачивается как фасад.

Эталон — витрина 800×400×900 с двумя стеклянными дверями:
- 2 элемента, площадь 0,7114 м², стекло 1 778,56 ₽;
- строки «фасад» нет (дверь не оплачивается дважды);
- каждая строка стекла знает свой module_id/door_index.
"""

import pytest

from app.services.calc_engine import _add_glass_to_modules, _derive_glass_items
from app.services.calc_engine import calculate_full_cost

MODULE = {
    "type": "lower_base",
    "width": 800,
    "depth": 400,
    "height": 900,
    "quantity": 1,
    "facades": {"count": 2, "type": "glass"},
    "has_glass": True,
}


def _materials():
    return {
        "ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
        "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000, "price_unit": "м²"},
        "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}],
    }


def _hardware():
    return {"hinges": {"price": 200, "brand": "FIRMAX"}, "handles": {"price": 500}}


def test_derive_glass_items_one_per_door():
    items = _derive_glass_items([MODULE])
    assert len(items) == 2
    assert {item["door_index"] for item in items} == {0, 1}
    assert all(item["module_id"] == 0 for item in items)
    assert all(item["width_mm"] == 397 for item in items)   # 800/2 − 3
    assert all(item["height_mm"] == 896 for item in items)  # 900 − 4


def test_glass_double_door_area_and_cost():
    result = calculate_full_cost(
        [dict(MODULE)], _materials(), _hardware(), [],
        zone_type="Гостиная",
    )
    # 2 × 0,397 × 0,896 = 0,711424 м²; × 2500 ₽/м² = 1778,56 ₽
    assert result.glass_calc.total_area_m2 == pytest.approx(0.7114, abs=1e-4)
    assert result.glass_cost == pytest.approx(1778.56, abs=0.01)

    glass_lines = [line for line in result.lines if line.kind == "glass"]
    assert len(glass_lines) == 2
    assert {line.door_index for line in glass_lines} == {0, 1}
    assert sum(line.amount for line in glass_lines) == pytest.approx(
        result.glass_cost, abs=0.01
    )


def test_glass_door_is_not_paid_as_facade():
    """Витрина не даёт одновременно строку фасада и стекла."""
    result = calculate_full_cost(
        [dict(MODULE)], _materials(), _hardware(), [],
        zone_type="Гостиная",
    )
    assert result.facade_cost == 0
    assert not [line for line in result.lines if line.kind == "facade"]
    assert result.glass_cost > 0


def test_module_without_glass_keeps_facade():
    module = dict(MODULE)
    module["has_glass"] = False
    module["facades"] = {"count": 2, "type": "doors"}
    result = calculate_full_cost(
        [module], _materials(), _hardware(), [], zone_type="Гостиная"
    )
    assert result.glass_cost == 0
    assert result.facade_cost > 0


def test_mixed_glass_without_price_blocks_kp():
    """Смешанное стекло: один элемент без цены → missing и блок КП (P2-13)."""
    items = [
        {"type": "clear", "width_mm": 600, "height_mm": 800},
        {"type": "graphite", "width_mm": 600, "height_mm": 800},
    ]
    result = calculate_full_cost(
        [dict(MODULE)], _materials(), _hardware(), items, zone_type="Гостиная"
    )
    assert result.glass_cost > 0
    assert result.can_issue_kp is False
    assert any("Стекло" in item for item in result.missing_prices)
    assert any(line.kind == "glass" and line.price_source == "missing"
               for line in result.lines)


def test_glass_item_without_type_is_not_keyerror():
    items = [{"width_mm": 600, "height_mm": 800}]
    result = calculate_full_cost(
        [dict(MODULE)], _materials(), _hardware(), items, zone_type="Гостиная"
    )
    assert result.can_issue_kp is False
    assert any(line.kind == "glass" and line.price_source == "missing"
               for line in result.lines)


def test_glass_is_bound_to_its_own_module():
    modules = [
        {"type": "lower_base", "width": 600, "depth": 560, "height": 820,
         "facades": {"count": 1, "type": "doors"}},
        {"type": "lower_base", "width": 800, "depth": 400, "height": 900,
         "facades": {"count": 2, "type": "glass"}, "has_glass": True},
    ]
    items = _derive_glass_items(modules)
    assert len(items) == 2
    assert all(item["module_id"] == 1 for item in items)

    bound = _add_glass_to_modules(modules, items)
    assert "glass_items" not in bound[0] or not bound[0].get("glass_items")
    assert len(bound[1]["glass_items"]) == 2
