"""История редакций веб-расчёта (этап 2)."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.services import web_quote_versions


def run(coro):
    return asyncio.run(coro)


def test_save_and_list_versions(tmp_path, monkeypatch) -> None:
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'versions.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(
            web_quote_versions,
            "AsyncSessionLocal",
            async_sessionmaker(engine, expire_on_commit=False),
        )
        try:
            first = await web_quote_versions.save_web_quote_version(
                "q" * 32,
                {
                    "quote": {},
                    "modules": [{"width": 600}],
                    "validation": {"can_calculate": True},
                },
                reason="initial",
            )
            second = await web_quote_versions.save_web_quote_version(
                "q" * 32,
                {
                    "quote": {},
                    "modules": [],
                    "validation": {"can_calculate": False},
                },
                reason="web-edit",
                created_by="manager",
            )
            history = await web_quote_versions.list_web_quote_versions("q" * 32)
            return first, second, history
        finally:
            await engine.dispose()

    first, second, history = run(scenario())
    assert first == 1
    assert second == 2
    assert [item["version_no"] for item in history] == [1, 2]
    assert history[0]["reason"] == "initial"
    assert history[1]["created_by"] == "manager"
    assert history[0]["modules_count"] == 1
    assert history[0]["can_calculate"] is True
    assert history[1]["can_calculate"] is False


def test_unknown_quote_has_empty_history(tmp_path, monkeypatch) -> None:
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'versions.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(
            web_quote_versions,
            "AsyncSessionLocal",
            async_sessionmaker(engine, expire_on_commit=False),
        )
        try:
            return await web_quote_versions.list_web_quote_versions("x" * 32)
        finally:
            await engine.dispose()

    assert run(scenario()) == []
