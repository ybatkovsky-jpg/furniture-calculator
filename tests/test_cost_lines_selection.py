"""P2-24/P2-07: цена из выбранной позиции и видимая подмена бренда.

P2-24: для списка позиций берётся первая выбранная с ценой, а не среднее
арифметическое (раньше «5100 и 5600» превращались в 5350 ₽ — цены, которой
нет ни в прайсе, ни в выборе оператора).
P2-07: кромка другого бренда, чем ЛДСП, больше не подменяется молча — в
предупреждениях расчёта появляется строка.
"""

from app.domain.cost_lines import _price_from
from app.services.calc_engine import calculate_full_cost


def test_list_price_is_first_selected_not_average():
    assert _price_from([{"price": 5100}, {"price": 5600}]) == 5100
    assert _price_from([{"price": None}, {"price": 5600}]) == 5600
    assert _price_from([{"price": 0}, {"unit_price": 1234}]) == 1234


def test_list_without_prices_returns_none():
    assert _price_from([{"price": 0}, {"price": None}]) is None
    assert _price_from([]) is None


def test_edge_brand_mismatch_is_visible():
    result = calculate_full_cost(
        [
            {
                "type": "lower_base", "width": 600, "depth": 560, "height": 820,
                "quantity": 1, "facades": {"count": 1}, "shelves": 1,
            }
        ],
        {
            "ldsp": {
                "name": "ЛДСП LAMARTY", "brand": "LAMARTY",
                "price": 5600, "price_unit": "лист",
            },
            "facades": {"name": "ФАСАДЫ ПВХ", "price": 6000, "price_unit": "м²"},
            "edge": {
                "name": "Кромка EGGER 0.8", "brand": "EGGER",
                "price": 34, "thickness_mm": 0.8,
            },
        },
        {"hinges": {"price": 172}, "handles": {"price": 550}},
        [],
        zone_type="Гостиная",
        price_book={
            "hdf": 1380, "confirmat": 2, "adjustable_feet": 50,
            "plinth": 1500, "edge_0.4": 17.5,
        },
    )
    assert any("не совпадает" in warning for warning in result.warnings)
