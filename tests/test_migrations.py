"""P1-12: схема обновляется миграциями Alembic, а не молчаливым create_all.

Проверяем на чистой БД: `init_db()` доводит схему до head, добавляет колонки,
которых нет в baseline (quote/zone_type/fastener_cost/thickness_mm/pack_size),
и повторный запуск ничего не ломает.
"""

import asyncio
import sqlite3

from app.config import settings
from app.db.session import init_db


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"pragma table_info({table})")}


def test_init_db_applies_migrations_to_fresh_database(tmp_path):
    db_path = tmp_path / "fresh.db"
    original_url = settings.database_url
    settings.database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    try:
        asyncio.run(init_db())
        asyncio.run(init_db())  # повторный старт: upgrade head — no-op
    finally:
        settings.database_url = original_url

    connection = sqlite3.connect(db_path)
    try:
        assert {"quote", "zone_type", "fastener_cost"} <= _columns(
            connection, "calculations"
        )
        assert {"thickness_mm", "pack_size", "sku"} <= _columns(
            connection, "price_items"
        )
        assert {"sheet_width_mm", "sheet_height_mm"} <= _columns(
            connection, "price_items"
        )
        assert {"recognition_confidence", "quality_score"} <= _columns(
            connection, "calculations"
        )
        version_columns = _columns(connection, "calculation_versions")
        assert {"calculation_id", "version_no", "quote", "reason"} <= version_columns
        assert {"id", "project_id", "quote", "modules", "validation"} <= _columns(
            connection, "web_quotes"
        )
        assert {"project_id", "status"} <= _columns(connection, "project_statuses")
        assert {"quote_id", "version_no", "modules", "reason", "validation"} <= _columns(
            connection, "web_quote_versions"
        )
        revision = connection.execute(
            "select version_num from alembic_version"
        ).fetchone()[0]
        assert revision == "a8c1e3f5b7d9"
    finally:
        connection.close()


def test_init_db_keeps_existing_data(tmp_path):
    """Миграции не удаляют данные: строка, вставленная до upgrade, на месте."""
    db_path = tmp_path / "existing.db"
    original_url = settings.database_url
    settings.database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    try:
        asyncio.run(init_db())
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                "insert into projects (telegram_user_id, client_name, client_phone) "
                "values ('42', 'Клиент', '+70000000000')"
            )
            connection.commit()
        finally:
            connection.close()
        asyncio.run(init_db())
    finally:
        settings.database_url = original_url

    connection = sqlite3.connect(db_path)
    try:
        count = connection.execute("select count(*) from projects").fetchone()[0]
        assert count == 1
    finally:
        connection.close()
