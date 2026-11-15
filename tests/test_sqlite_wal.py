"""P2-13: SQLite работает в WAL с busy_timeout (проверка боевого движка)."""

import asyncio

from sqlalchemy import text

from app.db.session import engine


def test_app_engine_enables_wal_and_busy_timeout():
    async def scenario():
        async with engine.connect() as conn:
            mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
            timeout = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
            fk = (await conn.execute(text("PRAGMA foreign_keys"))).scalar()
        return mode, timeout, fk

    mode, timeout, fk = asyncio.run(scenario())
    assert str(mode).lower() == "wal"
    assert int(timeout) >= 5000
    assert int(fk) == 1
