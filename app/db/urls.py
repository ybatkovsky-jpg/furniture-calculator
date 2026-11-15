"""Нормализация URL БД для sync- и async-драйверов (S5-04)."""

from __future__ import annotations


def sync_database_url(url: str) -> str:
    """URL для Alembic (синхронный драйвер).

    SQLite: `sqlite+aiosqlite` → `sqlite`.
    PostgreSQL: `postgresql+asyncpg` → `postgresql+psycopg` (psycopg 3).
    """
    if not url:
        return url
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def is_postgres(url: str) -> bool:
    return str(url or "").startswith(("postgresql", "postgres"))
