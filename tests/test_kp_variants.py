"""Варианты КП: три ступени, цены растут, скидка применяется.

Каталог подставляется синтетический — тесты не зависят от рабочей БД
(в CI её нет: `data/furniture.db` в .gitignore).
"""

from __future__ import annotations

from app.services.kp_variants import build_variants

MODULES = [
    {
        "type": "lower_base",
        "width": 800,
        "depth": 560,
        "height": 820,
        "quantity": 2,
        "facades": {"count": 2},
        "drawers": {"count": 1},
    },
    {
        "type": "upper_base",
        "width": 800,
        "depth": 320,
        "height": 720,
        "quantity": 2,
        "facades": {"count": 2},
        "drawers": {"count": 0},
    },
]


def _item(item_id: int, name: str, price: float, unit: str = "шт", **extra) -> dict:
    return {
        "id": item_id,
        "name": name,
        "unit_price": price,
        "price_unit": unit,
        "thickness_mm": extra.get("thickness_mm", 16),
        "brand": extra.get("brand"),
    }


def catalog() -> dict[str, list[dict]]:
    """Минимальный прайс: по две-три позиции на категорию, по возрастанию цены."""
    return {
        "ldsp": [
            _item(1, "ТОМЛЕСДРЕВ белый", 3600, "лист"),
            _item(2, "EGGER ЛДСП", 6650, "лист"),
            _item(3, "LAMARTY однотон", 7000, "лист"),
        ],
        "edge": [
            _item(4, "EXTRAVERT 0,4*19", 18, "м"),
            _item(5, "AGT 1*22", 80, "м"),
        ],
        "facades": [
            _item(6, "IVEGO ПВХ I категория", 3900, "м²"),
            _item(7, "ФАСАДЫ АКРИЛОВЫЕ 18ММ МАТОВЫЙ", 6300, "м²"),
            _item(8, "ФАСАДЫ АКРИЛОВЫЕ 18ММ ГЛЯНЕЦ МЕТАЛЛИК", 6900, "м²"),
        ],
        "hinges": [
            _item(9, "Петля EVO накладная 90°", 200),
            _item(10, "Петля Боярд EVO 90°", 310),
            _item(11, "Петля Blum накладная 110°", 550),
        ],
        "drawers": [
            _item(12, "Ящик СТАРТ h=86", 1656),
            _item(13, "Ящик Avantech You 101", 4875),
            _item(14, "Ящик LEGRABOX 144", 10445),
        ],
    }


def test_three_variants_in_order() -> None:
    variants = build_variants(MODULES, zone_type="Кухня", catalog=catalog())
    assert [variant.name for variant in variants] == ["Базовая", "Стандарт", "Комфорт"]


def test_prices_grow_from_base_to_comfort() -> None:
    variants = build_variants(MODULES, zone_type="Кухня", catalog=catalog())
    prices = [variant.final_cash for variant in variants]
    assert prices == sorted(prices)
    assert prices[-1] > prices[0]


def test_noncash_is_higher_than_cash() -> None:
    for variant in build_variants(MODULES, zone_type="Кухня", catalog=catalog()):
        assert variant.final_noncash > variant.final_cash


def test_discount_lowers_every_variant() -> None:
    full = build_variants(MODULES, zone_type="Кухня", catalog=catalog())
    discounted = build_variants(
        MODULES, zone_type="Кухня", catalog=catalog(), discount_percent=10
    )
    for before, after in zip(full, discounted):
        assert after.final_cash < before.final_cash
        assert abs(after.final_cash - before.final_cash * 0.9) < 1


def test_variants_differ_by_materials() -> None:
    variants = build_variants(MODULES, zone_type="Кухня", catalog=catalog())
    assert len({variant.materials_text for variant in variants}) == len(variants)
    assert "ТОМЛЕСДРЕВ" in variants[0].materials_text
    assert "LAMARTY" in variants[-1].materials_text


def test_catalog_reads_database(tmp_path, monkeypatch) -> None:
    """Каталог берётся из прайса БД: категории, фильтр лишних позиций, м²-фасады."""
    import sqlite3

    from app.services import kp_variants

    database = tmp_path / "prices.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "create table price_items (id integer primary key, name text, category text, "
        "unit_price real, price_unit text, thickness_mm integer, brand text, is_active integer)"
    )
    rows = [
        (1, "ТОМЛЕСДРЕВ белый", "ЛДСП", 3600, "лист", 16, None, 1),
        (2, "EGGER ЛДСП", "ЛДСП", 6650, "лист", 16, "EGGER", 1),
        (3, "Стеновая панель ЛДСП", "ЛДСП", 9000, "лист", 8, None, 1),
        (4, "AGT 1*22", "Кромка", 80, "м", None, None, 1),
        (5, "IVEGO ПВХ", "ЭФТРИ ПВХ", 3900, "м²", None, None, 1),
        (6, "EVOSOFT 18мм", "МДФ плиты", 10200, "м", None, None, 1),
        (7, "Петля EVO накладная", "Петли", 200, "шт", None, None, 1),
        (8, "Ящик СТАРТ", "Ящики", 1656, "шт", None, None, 1),
        (9, "Неактивная", "ЛДСП", 100, "лист", 16, None, 0),
    ]
    connection.executemany(
        "insert into price_items (id, name, category, unit_price, price_unit, thickness_mm, brand, is_active) "
        "values (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    connection.commit()
    connection.close()

    monkeypatch.setattr(kp_variants, "DB_PATH", database)
    catalog_data = kp_variants._catalog()
    names = [item["name"] for item in catalog_data["ldsp"]]
    assert "ТОМЛЕСДРЕВ белый" in names
    assert "Стеновая панель ЛДСП" not in names  # исключена как не-материал корпуса
    assert all(item["price_unit"] == "м²" for item in catalog_data["facades"])
    assert len(catalog_data["hinges"]) == 1
