"""P1-05: МДФ-фасад с ценой за лист пересчитывается в м² по формату листа.

Типовой сценарий «кухня с EMDIWAY/AGT/EVOGLOSS»: цена за лист, расход — м².
Раньше строка не создавалась вовсе, КП блокировался; без формата листа теперь
КП блокируется с понятной причиной, а не молчаливым нулём.
"""

import pytest

from app.services.calc_engine import calculate_full_cost


def _module():
    return {
        "type": "lower_base", "width": 600, "depth": 560, "height": 820,
        "quantity": 1, "facades": {"count": 1, "type": "doors"}, "shelves": 1,
    }


def _materials(sheet_width=None, sheet_height=None, price=9800):
    facades = {
        "name": "EMDIWAY U702", "price": price, "price_unit": "лист",
        "thickness_mm": 18,
    }
    if sheet_width and sheet_height:
        facades["sheet_width_mm"] = sheet_width
        facades["sheet_height_mm"] = sheet_height
    return {
        "ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
        "facades": facades,
        "edge": [{"name": "Кромка МДФ 1*22", "thickness_mm": 1.0, "price": 45}],
    }


def _hardware():
    return {"hinges": {"price": 200, "brand": "FIRMAX"}, "handles": {"price": 500}}


def _calc(materials):
    return calculate_full_cost(
        [_module()], materials, _hardware(), [],
        zone_type="Гостиная",
        price_book={
            "hdf": 1530, "confirmat": 2, "adjustable_feet": 50,
            "wall_mount": 120, "plinth": 1500,
            "edge_0.4": 17.5, "edge_0.8": 34,
        },
    )


def test_sheet_price_is_converted_to_m2_by_format():
    result = _calc(_materials(2800, 2070, 9800))
    facades = [line for line in result.lines if line.kind == "facade"]
    assert len(facades) == 1
    line = facades[0]
    # 9800 / (2,8 × 2,07 = 5,796 м²) = 1690,82 ₽/м²
    assert line.unit_price == pytest.approx(1690.82, abs=0.01)
    assert line.unit == "м²"
    assert "пересчёт с листа" in line.note
    assert result.can_issue_kp is True


def test_three_square_metres_cost_is_5072_46():
    module = _module()
    module["facades"] = {"count": 2, "type": "doors"}
    result = calculate_full_cost(
        [module], _materials(2800, 2070, 9800), _hardware(), [],
        zone_type="Гостиная",
        price_book={
            "hdf": 1530, "confirmat": 2, "adjustable_feet": 50,
            "wall_mount": 120, "plinth": 1500,
            "edge_0.4": 17.5, "edge_0.8": 34,
        },
    )
    facade = [line for line in result.lines if line.kind == "facade"][0]
    expected_area = round(
        ((600 / 2 - 3) / 1000) * ((820 - 4) / 1000) * 2, 3
    )
    assert facade.quantity == pytest.approx(expected_area, abs=0.001)
    assert facade.amount == pytest.approx(result.facade_cost, abs=0.01)


def test_missing_sheet_format_blocks_kp():
    result = _calc(_materials())
    assert result.can_issue_kp is False
    assert any("формат листа" in item for item in result.missing_prices)
    assert any("формат листа" in w for w in result.warnings)


def test_square_metre_price_is_not_converted():
    """Если цена уже за м², формат листа не нужен и строка обычная."""
    materials = _materials()
    materials["facades"] = {
        "name": "ПВХ", "price": 10000, "price_unit": "м²",
    }
    result = _calc(materials)
    facade = [line for line in result.lines if line.kind == "facade"][0]
    assert facade.unit_price == 10000
    assert result.can_issue_kp is True
