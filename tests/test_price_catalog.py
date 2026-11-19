"""День 2: единый каталог цен и консервативный подбор для Excel/чата."""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, PriceItem
from app.services.price_catalog import (
    PriceCatalogItem,
    find_price,
    load_price_catalog,
    normalize_unit,
)


def _item(item_id, name, category, price, unit="шт", brand=None, thickness=None):
    return PriceCatalogItem(item_id, name, category, brand, price, unit, thickness)


def test_unit_aliases_are_same_unit():
    assert normalize_unit("м.п.") == "м"
    assert normalize_unit("листов") == "лист"
    assert normalize_unit("м2") == "м²"
    assert normalize_unit("ШТ") == "шт"


def test_exact_match_beats_superstring_and_other_price():
    """«LEGRABOX» не должен получить цену «LEGRABOX TIP ON»."""
    catalog = [
        _item(1, "Ящик стандартный для низких фасадов LEGRABOX", "Ящики", 7230),
        _item(2, "Ящик стандартный для низких фасадов LEGRABOX TIP ON", "Ящики", 10445),
    ]
    match = find_price(catalog, ["Ящик стандартный для низких фасадов LEGRABOX"], "шт")
    assert match is not None
    assert match.item.id == 1
    assert match.item.price == 7230


def test_template_qualifier_not_in_price_is_ignored_when_unique():
    """«однотон»/«МФ-ГРУПП» — уточнения шаблона, которых нет в прайсе."""
    catalog = [
        _item(1, "EGGER ЛДСП", "ЛДСП", 6650, "лист", "EGGER"),
        _item(2, "LAMARTY однотон", "ЛДСП", 5100, "лист", "LAMARTY"),
    ]
    plain = find_price(catalog, ["EGGER ЛДСП", "однотон"], "листов")
    assert plain is not None and plain.item.id == 1

    lamarty = find_price(catalog, ["LAMARTY", "однотон"], "листов")
    assert lamarty is not None and lamarty.item.price == 5100


def test_ambiguous_prices_are_not_guessed():
    """Два разных декора ETERNO — цена не определена, а не первая попавшаяся."""
    catalog = [
        _item(1, "МДФ ETERNO (SANSATION) 1*22", "Кромка", 88, "м", thickness=1.0),
        _item(2, "МДФ ETERNO (MTT) 1*22", "Кромка", 91, "м", thickness=1.0),
    ]
    assert find_price(catalog, ["ETERNO", "1*22"], "м.п.") is None


def test_ambiguous_same_price_is_resolved_deterministically():
    catalog = [
        _item(2, "Позиция Б", "Кромка", 50, "м"),
        _item(1, "Позиция А", "Кромка", 50, "м"),
    ]
    match = find_price(catalog, ["Позиция"], "м")
    assert match is not None and match.item.id == 1


def test_unit_and_thickness_filters_are_applied():
    catalog = [
        _item(1, "EGGER 0,8*19", "Кромка", 34, "м", "EGGER", 0.8),
        _item(2, "EGGER 2*19", "Кромка", 60, "м", "EGGER", 2.0),
    ]
    assert find_price(catalog, ["EGGER 0,8*19"], "лист") is None
    match = find_price(catalog, ["EGGER 2*19"], "м", thickness=2.0)
    assert match is not None and match.item.id == 2
    match = find_price(catalog, ["EGGER"], "м", brand="EGGER", category="Кромка")
    # Одно общее слово «EGGER» — не повод угадывать: два разных реза с
    # разными ценами, значит цены нет.
    assert match is None
    match = find_price(catalog, ["EGGER 0,8*19"], "м", brand="EGGER", category="Кромка")
    assert match is not None and match.item.id == 1


def test_led_coil_price_uses_meter_price_times_five():
    from app.services.template_filler import _resolve_row_price

    catalog = [_item(1, "Подсветка LED (Бухта 5м)", "Подсветка", 364, "м")]
    price = _resolve_row_price(catalog, "led_strip_5m", ["Подсветка LED (Бухта 5м)"], "шт")
    assert price == 1820


def test_load_price_catalog_excludes_manual_and_empty_prices():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add_all([
                PriceItem(name="EGGER ЛДСП", category="ЛДСП", unit_price=6650,
                          price_unit="лист", is_active=1, requires_manual_price=0),
                PriceItem(name="Ручная", category="Ручки", unit_price=None,
                          price_unit="шт", is_active=1, requires_manual_price=1),
                PriceItem(name="Старая", category="ЛДСП", unit_price=1,
                          price_unit="лист", is_active=0),
            ])
            await session.commit()
            catalog = await load_price_catalog(session)
        await engine.dispose()
        return catalog

    catalog = asyncio.run(scenario())
    assert [item.name for item in catalog] == ["EGGER ЛДСП"]
