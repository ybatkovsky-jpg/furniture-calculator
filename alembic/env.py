"""Alembic-окружение: метаданные моделей и URL из настроек.

До P1-15b здесь стоял `target_metadata = None`, поэтому autogenerate не видел
моделей, а `alembic/versions` был пуст: схема создавалась только `create_all`.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

from app.config import settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Схема приложения — единый источник для autogenerate.
target_metadata = Base.metadata

# Alembic работает синхронным драйвером, а приложение — асинхронным.
_sync_url = settings.database_url.replace("+aiosqlite", "").replace(
    "+asyncpg", "+psycopg2"
)
config.set_main_option("sqlalchemy.url", _sync_url)


def run_migrations_offline() -> None:
    """Offline-режим: только SQL, без подключения к БД."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online-режим: применяем миграции к реальной БД."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,  # SQLite: ALTER через batch-режим
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
