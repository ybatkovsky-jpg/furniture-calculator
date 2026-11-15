"""P0-02/P1-09: подбор цен из прайса для полного CostBreakdown."""

from types import SimpleNamespace

from app.services.price_book import build_price_book


def _item(name, category, price, unit="шт", thickness=None):
    return SimpleNamespace(
        name=name, category=category, unit_price=price,
        price_unit=unit, thickness_mm=thickness,
    )


def test_hdf_is_found_by_name_or_category():
    book = build_price_book([_item("ХДФ", "ХДФ", 1380, "лист")])
    assert book["hdf"] == 1380


def test_gola_horizontal_and_vertical_are_distinct():
    book = build_price_book([
        _item("GOLA профиль горизонтальный 3 м", "GOLA", 3550, "шт"),
        _item("GOLA профиль вертикальный боковой 3 м", "GOLA", 1835, "шт"),
    ])
    assert book["gola_horizontal"] == 3550
    assert book["gola_vertical"] == 1835


def test_led_coil_is_per_metre_and_power_supply_per_piece():
    book = build_price_book([
        _item("Подсветка LED (Бухта 5м)", "Подсветка", 364, "м"),
        _item("Блок питания", "Подсветка", 1171, "шт"),
        _item("Датчик", "Подсветка", 1555, "шт"),
    ])
    assert book["led_strip"] == 364
    assert book["led_power"] == 1171
    assert book["led_sensor"] == 1555


def test_wrong_unit_is_not_used():
    """Цена столешницы за м² не должна попасть в строку «за метр»."""
    book = build_price_book([_item("Столешница", "Столешницы", 9999, "м²")])
    assert "countertop" not in book


def test_edge_prices_by_thickness():
    book = build_price_book([
        _item("EGGER 0,8*19", "Кромка", 34, "м", thickness=0.8),
        _item("EGGER 0,4*19", "Кромка", 17.5, "м", thickness=0.4),
        _item("EVO 1*22", "Кромка", 45, "м", thickness=1.0),
    ])
    assert book["edge_0.8"] == 34
    assert book["edge_0.4"] == 17.5
    assert book["edge_mdf_1mm"] == 45


def test_fasteners_are_found():
    book = build_price_book([
        _item("Конфирмат 7×50", "Крепёж", 2, "шт"),
        _item("Регулируемая опора", "Крепёж", 50, "шт"),
        _item("Настенный подвес (регулируемый)", "Крепёж", 120, "шт"),
        _item("Цоколь ПВХ Rehau 4м", "Крепёж", 1500, "шт"),
    ])
    assert book["confirmat"] == 2
    assert book["adjustable_feet"] == 50
    assert book["wall_mount"] == 120
    assert book["plinth"] == 1500
