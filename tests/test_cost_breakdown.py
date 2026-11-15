"""P0-02: полный CostBreakdown — каждая позиция становится строкой."""

from app.services.calc_engine import calculate_full_cost


def _module():
    return {
        "type": "lower_base",
        "width": 600,
        "depth": 560,
        "height": 820,
        "quantity": 1,
        "facades": {"count": 2, "type": "doors"},
        "drawers": {"count": 2},
        "shelves": 1,
    }


def _materials():
    return {
        "ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
        "facades": {"name": "MDF", "price": 10000, "price_unit": "м²"},
        "edge": [
            {"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34},
            {"name": "Кромка 0.4", "thickness_mm": 0.4, "price": 17.5},
        ],
    }


def _hardware():
    return {
        "hinges": {"price": 200, "brand": "FIRMAX"},
        "drawers": {"price": 3000, "name": "Tandembox"},
        "handles": {"price": 500},
    }


def _price_book():
    return {
        "hdf": 1530,
        "confirmat": 2,
        "adjustable_feet": 50,
        "wall_mount": 120,
        "plinth": 1500,
    }


def _calc(**kwargs):
    return calculate_full_cost(
        [_module()],
        _materials(),
        _hardware(),
        [],
        zone_type="Гостиная",  # не кухня: без столешницы/GOLA
        price_book=_price_book(),
        **kwargs,
    )


def test_golden_module_600x560x820_two_doors_two_drawers():
    """Эталон: ЛДСП 6000, кромка 549, фасады 4850, ХДФ 1530,
    петли 800, ящики 6000, ручки 1000, крепёж 1744."""
    result = _calc()
    totals = {}
    for line in result.lines:
        totals[line.kind] = totals.get(line.kind, 0) + line.amount

    assert totals["ldsp"] == 6000
    assert totals["edge"] == 549          # 374 (0.8) + 175 (0.4)
    assert totals["facade"] == 4850       # 0.485 м² × 10 000
    assert totals["hdf"] == 1530
    assert totals["hinge"] == 800         # 4 петли × 200
    assert totals["drawer"] == 6000       # 2 ящика × 3000
    assert totals["handle"] == 1000       # 2 ручки × 500
    assert totals["fastener"] == 1744

    assert result.cost_breakdown.cost_price == 22473
    assert abs(sum(line.amount for line in result.lines) - result.cost_breakdown.cost_price) <= 0.01
    assert result.can_issue_kp is True
    assert result.warnings == []


def test_every_zero_line_has_missing_marker():
    """Нет строк «количество > 0, сумма 0 без пометки»."""
    result = _calc()
    for line in result.lines:
        assert line.amount > 0 or line.price_source == "missing", line


def test_edge_counted_without_module_details():
    """Кромка считается из quantities, а не из module['details'] (их нет)."""
    result = _calc()
    assert result.edge_cost > 0
    assert {l.kind for l in result.lines} >= {"edge"}


def test_drawers_as_dict_are_priced():
    """UI сохраняет drawers как dict; раньше извлекатель ждал list → 0 ₽."""
    result = _calc()
    drawer_lines = [line for line in result.lines if line.kind == "drawer"]
    assert drawer_lines and drawer_lines[0].amount == 6000


def test_drawers_as_list_are_priced_too():
    hardware = _hardware()
    hardware["drawers"] = [{"price": 3000, "brand": "BLUM"}]
    result = calculate_full_cost(
        [_module()], _materials(), hardware, [], zone_type="Гостиная",
        price_book=_price_book(),
    )
    assert result.hardware_cost >= 6000


def test_missing_drawer_price_is_marked_not_zero():
    hardware = _hardware()
    hardware.pop("drawers")
    result = calculate_full_cost(
        [_module()], _materials(), hardware, [], zone_type="Гостиная",
        price_book=_price_book(),
    )
    drawer_lines = [l for l in result.lines if l.kind == "drawer"]
    assert drawer_lines and drawer_lines[0].price_source == "missing"
    assert any("Ящики" in m for m in result.missing_prices)
    assert result.can_issue_kp is False


def test_missing_handles_block_kp():
    hardware = _hardware()
    hardware.pop("handles")
    result = calculate_full_cost(
        [_module()], _materials(), hardware, [], zone_type="Гостиная",
        price_book=_price_book(),
    )
    assert result.can_issue_kp is False
    assert any("Ручки" in w for w in result.warnings)


def test_missing_price_book_blocks_kp_but_keeps_lines():
    """Без цен ХДФ/крепежа строки остаются с пометкой «цена не задана»."""
    result = calculate_full_cost(
        [_module()], _materials(), _hardware(), [], zone_type="Гостиная",
        price_book={},
    )
    assert result.can_issue_kp is False
    assert any("ХДФ" in m for m in result.missing_prices)
    assert any(l.price_source == "missing" for l in result.lines)


def test_glass_facade_priced_without_explicit_sizes():
    """P1-19: has_glass + фасады → стекло тарифицируется по геометрии двери."""
    module = _module()
    module["has_glass"] = True
    result = calculate_full_cost(
        [module], _materials(), _hardware(), [], zone_type="Гостиная",
        price_book=_price_book(),
    )
    assert result.glass_cost > 0
    assert any(line.kind == "glass" for line in result.lines)
    assert result.can_issue_kp is True


def test_unknown_facade_not_double_counted_in_ldsp():
    """P1-17: тип фасада unknown не добавляет площадь в ЛДСП."""
    materials = _materials()
    materials["facades"] = {"name": "Фасад без типа", "price": 10000}
    result = calculate_full_cost(
        [_module()], materials, _hardware(), [], zone_type="Гостиная",
        price_book=_price_book(),
    )
    # Один лист ЛДСП (без +15% площади фасадов).
    assert [l for l in result.lines if l.kind == "ldsp"][0].quantity == 1
