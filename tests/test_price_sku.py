"""Этап 5: артикул поставщика (SKU) у позиции прайса."""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.price import PriceItem
from app.services.price_catalog import PriceCatalogItem


def test_price_item_has_sku(tmp_path) -> None:
    async def scenario() -> str | None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'price.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add(
                PriceItem(
                    name="Конфирмат 7×50",
                    sku="KONF-7X50",
                    category="Крепёж",
                    unit_price=2.0,
                    price_unit="шт",
                )
            )
            await session.commit()
            item = (await session.execute(select(PriceItem))).scalar_one()
            result = item.sku
        await engine.dispose()
        return result

    assert asyncio.run(scenario()) == "KONF-7X50"


def test_catalog_item_has_sku_or_internal_id() -> None:
    item = PriceCatalogItem(
        id=523,
        name="Конфирмат 7×50",
        category="Крепёж",
        brand=None,
        price=2.0,
        unit="шт",
        thickness_mm=None,
        sku=None,
    )
    assert item.sku_or_id == "ID-523"
