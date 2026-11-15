"""S5-04: URL БД для sync/async драйверов."""

from app.db.urls import is_postgres, sync_database_url


def test_sqlite_url_for_alembic():
    assert sync_database_url("sqlite+aiosqlite:///./data/furniture.db") == (
        "sqlite:///./data/furniture.db"
    )


def test_postgres_url_for_alembic_uses_psycopg():
    assert sync_database_url("postgresql+asyncpg://u:p@db:5432/furniture") == (
        "postgresql+psycopg://u:p@db:5432/furniture"
    )


def test_is_postgres():
    assert is_postgres("postgresql+asyncpg://u:p@db/db")
    assert not is_postgres("sqlite+aiosqlite:///db")
