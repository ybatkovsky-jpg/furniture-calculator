"""Статусы импортированных проектов в БД (этап 1D)."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.services import project_status_store


def run(coro):
    return asyncio.run(coro)


def test_set_and_get_project_status(tmp_path, monkeypatch) -> None:
    async def scenario() -> tuple[bool, str | None, str | None]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'status.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(
            project_status_store,
            "AsyncSessionLocal",
            async_sessionmaker(engine, expire_on_commit=False),
        )
        try:
            saved = await project_status_store.set_project_status(
                "a" * 16, "IMPORTED"
            )
            first = await project_status_store.get_project_status("a" * 16)
            await project_status_store.set_project_status("a" * 16, "CALCULATED")
            second = await project_status_store.get_project_status("a" * 16)
            return saved, first, second
        finally:
            await engine.dispose()

    saved, first, second = run(scenario())
    assert saved is True
    assert first == "IMPORTED"
    assert second == "CALCULATED"


def test_unknown_project_status_is_none(tmp_path, monkeypatch) -> None:
    async def scenario() -> str | None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'status.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(
            project_status_store,
            "AsyncSessionLocal",
            async_sessionmaker(engine, expire_on_commit=False),
        )
        try:
            return await project_status_store.get_project_status("b" * 16)
        finally:
            await engine.dispose()

    assert run(scenario()) is None
