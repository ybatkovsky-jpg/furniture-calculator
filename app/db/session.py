"""
Конфигурация SQLAlchemy сессий для асинхронной работы с БД.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Создаём асинхронный движок SQLite
engine = create_async_engine(
    settings.database_url,
    echo=False,  # Если True, будут выводиться SQL запросы в логи
    future=True,
)


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        """P2-13: WAL + busy_timeout, иначе читатель блокирует писателя."""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

# Асинхронная фабрика сессий
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Генератор для получения асинхронной сессии БД.
    
    Используется в Dependency Injection (FastAPI).
    Пример:
        async def my_handler(session: AsyncSession = Depends(get_session)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def _run_migrations_sync() -> None:
    """Применить `alembic upgrade head` синхронно (Alembic не async).

    P1-12: `create_all` не добавляет колонки в существующую БД, поэтому новый
    код на старой базе падал на `thickness_mm`/`quote`. Теперь схема
    обновляется миграциями; ошибка миграции — падение старта с понятным
    текстом, а не работа на неполной схеме.
    """
    from alembic.config import Config

    from alembic import command

    ini_path = _PROJECT_ROOT / "alembic.ini"
    if not ini_path.exists():
        raise RuntimeError(
            f"Не найден alembic.ini по пути {ini_path}. "
            "Запустите бота из корня проекта."
        )
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    try:
        command.upgrade(cfg, "head")
    except Exception as exc:
        raise RuntimeError(
            "Не удалось применить миграции Alembic. "
            "Сделайте копию БД и выполните `alembic upgrade head` вручную; "
            f"причина: {exc}"
        ) from exc


async def init_db():
    """Обновить схему БД миграциями Alembic (P1-12).

    Вызывается при старте приложения. `create_all` оставлен только для
    тестов/одноразовых скриптов (`Base.metadata.create_all`), потому что он
    не умеет добавлять колонки в уже существующие таблицы.
    """
    await asyncio.to_thread(_run_migrations_sync)
    logger.info("✓ Миграции Alembic применены (head)")


async def drop_db():
    """
    Удалить все таблицы (для тестирования).
    """
    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
