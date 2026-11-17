"""P1-09: каждая позиция с количеством получает строку в чате/КП.

Раньше штанги, сушки, лотки, бутылочницы, гигиенические поддоны и внутренние
ящики были только в Excel: `MaterialQuantities` их считал, `build_cost_lines`
не создавал строк — чат и КП занижали кухню/гардероб.
"""

from types import SimpleNamespace

from app.domain.cost_lines import build_cost_lines
from app.services.image_analyzer import RecognizedModule
from app.services.price_book import build_price_book
from app.services.quantity_calc import calculate_quantities


def _item(name, category, price, unit="шт", thickness=None):
    return SimpleNamespace(
        name=name, category=category, unit_price=price,
        price_unit=unit, thickness_mm=thickness,
    )


def _full_price_book():
    return build_price_book([
        _item("ХДФ 3мм", "ХДФ", 1530, "лист"),
        _item("Конфирмат 7×50", "Крепёж", 2),
        _item("Регулируемая опора", "Крепёж", 50),
        _item("Настенный подвес", "Крепёж", 120),
        _item("Цоколь ПВХ Rehau 4м", "Крепёж", 1500),
        _item("Штанга круглая D25", "Штанги", 407),
        _item("Штанга прямоугольная антискользящая", "Штанги", 900),
        _item("Выдвижная корзина 2-х уровн.(150) Flora BOYARD", "Бутылочницы", 3000),
        _item("Лоток для столовых приборов", "Комплектующие", 2500),
        _item("Сушка для посуды Alba, в модуль на 900мм", "Сушки", 2500),
        _item("Гигиенический поддон", "Комплектующие", 700),
        _item("Ящик внутренний TANDEMBOX МФ-ГРУПП", "Ящики", 9000),
        _item("EGGER 0,4*19", "Кромка", 17.5, "м", thickness=0.4),
        _item("EGGER 0,8*19", "Кромка", 34.0, "м", thickness=0.8),
        _item("Кромка МДФ 1*22", "Кромка", 45.0, "м", thickness=1.0),
        _item("Столешница постформинг", "Столешницы", 2834.63, "м"),
    ])


def _materials():
    return {
        "ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
        "facades": {"name": "ФАСАДЫ ПВХ", "price": 10000, "price_unit": "м²"},
        "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}],
    }


def _hardware():
    return {
        "hinges": {"price": 200, "brand": "FIRMAX"},
        "drawers": {"price": 3000},
        "handles": {"price": 500},
    }


FIELD_TO_KIND = {
    "rods_round_count": "rod",
    "rods_rectangular_count": "rod",
    "bottle_holder_count": "bottle_holder",
    "cutlery_tray_count": "cutlery_tray",
    "drying_rack_count": "drying_rack",
    "hygienic_mat_count": "hygienic_mat",
    "drawers_internal_count": "drawer_internal",
}


def test_kitchen_accessories_have_lines_and_prices():
    module = RecognizedModule(
        type="lower_base", width=200, depth=560, height=820,
        facades={"count": 1, "type": "doors"},
    )
    quantities = calculate_quantities(
        [module], "", [], zone_type="Кухня",
        auto_accessories=True, auto_drawers=True, has_sink=True,
    )
    assert quantities.cutlery_tray_count > 0
    assert quantities.drying_rack_count > 0
    assert quantities.bottle_holder_count > 0
    assert quantities.drawers_internal_count > 0

    result = build_cost_lines(
        quantities, _materials(), _hardware(), price_book=_full_price_book()
    )
    kinds = {line.kind for line in result.lines}
    for field_name, kind in FIELD_TO_KIND.items():
        if getattr(quantities, field_name) > 0:
            assert kind in kinds, f"{field_name} без строки {kind}"
    assert result.can_issue_kp is True
    assert sum(line.amount for line in result.lines) == result.total


def test_wardrobe_rods_have_lines():
    module = RecognizedModule(
        type="penal", width=600, depth=600, height=2400,
        facades={"count": 2, "type": "doors"},
    )
    quantities = calculate_quantities([module], "", [], zone_type="Гардеробная")
    assert quantities.rods_round_count > 0
    assert quantities.rods_rectangular_count > 0

    result = build_cost_lines(
        quantities, _materials(), _hardware(), price_book=_full_price_book()
    )
    rods = [line for line in result.lines if line.kind == "rod"]
    assert len(rods) == 2
    assert all(line.amount > 0 for line in rods)
    assert result.can_issue_kp is True


def test_accessory_without_price_blocks_kp():
    """Нет цены — строка остаётся, но помечена missing и блокирует КП."""
    module = RecognizedModule(
        type="lower_base", width=600, depth=560, height=820,
        facades={"count": 1, "type": "doors"},
    )
    quantities = calculate_quantities(
        [module], "", [], zone_type="Кухня", auto_accessories=True, has_sink=True,
    )
    result = build_cost_lines(quantities, _materials(), _hardware(), price_book={})
    assert result.can_issue_kp is False
    missing_kinds = {line.kind for line in result.lines if line.price_source == "missing"}
    assert {"cutlery_tray", "drying_rack"} <= missing_kinds
    assert any("Лоток" in item for item in result.missing)
