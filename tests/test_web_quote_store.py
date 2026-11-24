"""Постоянное хранение веб-расчётов: web_quotes (этап 1D)."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.services import web_quote_store


def run(coro):
    return asyncio.run(coro)


def test_save_and_load_web_quote(tmp_path, monkeypatch) -> None:
    async def scenario() -> dict | None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quotes.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(web_quote_store, "AsyncSessionLocal", session_factory)
        try:
            saved = await web_quote_store.save_web_quote(
                "quote-1",
                {
                    "quote": {"total_cash": 100},
                    "modules": [{"type": "lower_base", "width": 600}],
                    "validation": {"can_calculate": True, "values": []},
                    "zone": "Кухня",
                    "image": "page.jpg",
                    "project_id": "a" * 16,
                    "page_index": 0,
                },
            )
            assert saved is True
            return await web_quote_store.load_web_quote("quote-1")
        finally:
            await engine.dispose()

    loaded = run(scenario())
    assert loaded is not None
    assert loaded["quote"]["total_cash"] == 100
    assert loaded["project_id"] == "a" * 16
    assert loaded["zone"] == "Кухня"
    assert loaded["modules"][0]["width"] == 600
    assert loaded["validation"]["can_calculate"] is True


def test_load_missing_quote_returns_none(tmp_path, monkeypatch) -> None:
    async def scenario() -> dict | None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quotes.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(
            web_quote_store,
            "AsyncSessionLocal",
            async_sessionmaker(engine, expire_on_commit=False),
        )
        try:
            return await web_quote_store.load_web_quote("нет-такого")
        finally:
            await engine.dispose()

    assert run(scenario()) is None
