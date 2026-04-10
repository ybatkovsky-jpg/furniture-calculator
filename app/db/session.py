"""
Конфигурация SQLAlchemy сессий для асинхронной работы с БД.
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.config import settings
from typing import AsyncGenerator

# Создаём асинхронный движок SQLite
engine = create_async_engine(
    settings.database_url,
    echo=False,  # Если True, будут выводиться SQL запросы в логи
    future=True,
)

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


async def init_db():
    """
    Создать все таблицы в БД.
    
    Вызывается при старте приложения.
    """
    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_db():
    """
    Удалить все таблицы (для тестирования).
    """
    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
