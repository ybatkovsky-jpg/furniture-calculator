"""Перенос данных SQLite → PostgreSQL (S5-04).

Источник: `data/furniture.db` (SQLite).
Приёмник: `DATABASE_URL` (postgresql+psycopg://…), схема уже поднята Alembic.

Скрипт идемпотентен: существующие id не перезаписываются (ON CONFLICT DO NOTHING).
После вставки сбрасывает последовательности SERIAL на MAX(id).

Запуск:
    $env:DATABASE_URL = "postgresql+psycopg://user:pass@host:5432/furniture"
    python scripts/migrate_sqlite_to_postgres.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import Base

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate")

# Порядок важен: родители раньше детей.
TABLES = [
    "projects",
    "price_items",
    "glass_types",
    "calc_settings",
    "calculations",
    "commercial_offers",
    "price_history",
]


def migrate(source: Path, target_url: str) -> dict[str, int]:
    if not source.exists():
        raise FileNotFoundError(source)
    if not target_url.startswith("postgresql"):
        raise ValueError("DATABASE_URL должен быть postgresql+psycopg://…")

    src = create_engine(f"sqlite:///{source.as_posix()}")
    dst = create_engine(target_url)
    copied: dict[str, int] = {}

    with src.connect() as sconn, dst.begin() as dconn:
        for name in TABLES:
            table = Base.metadata.tables[name]
            rows = [dict(row._mapping) for row in sconn.execute(select(table))]
            if rows:
                stmt = pg_insert(table).values(rows).on_conflict_do_nothing()
                dconn.execute(stmt)
            db_count = dconn.execute(select(func.count()).select_from(table)).scalar_one()
            copied[name] = int(db_count)
            logger.info("%s: перенесено %d, в PostgreSQL %d", name, len(rows), db_count)

            if "id" in table.c:
                seq = dconn.execute(
                    text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": name}
                ).scalar()
                if seq:
                    dconn.execute(
                        text(
                            "SELECT setval(:seq, "
                            "COALESCE((SELECT MAX(id) FROM " + name + "), 1))"
                        ),
                        {"seq": seq},
                    )
    return copied


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="data/furniture.db")
    parser.add_argument("--target", default=os.getenv("DATABASE_URL", ""))
    args = parser.parse_args()
    counts = migrate(Path(args.source), args.target)
    logger.info("Готово: %s", counts)


if __name__ == "__main__":
    main()
