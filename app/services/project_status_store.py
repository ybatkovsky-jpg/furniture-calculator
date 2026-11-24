"""Запись статуса импортированного проекта (этап 1D)."""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from app.db.session import AsyncSessionLocal
from app.models.project_status import ProjectStatus

logger = logging.getLogger(__name__)


async def set_project_status(project_id: str, status: str) -> bool:
    """Записать статус; False — БД недоступна/таблицы нет."""
    try:
        async with AsyncSessionLocal() as session:
            row = await session.get(ProjectStatus, project_id)
            if row is None:
                row = ProjectStatus(project_id=project_id, status=status)
                session.add(row)
            else:
                row.status = status
            await session.commit()
        return True
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("Статус проекта %s не сохранён: %s", project_id, exc)
        return False


async def get_project_status(project_id: str) -> str | None:
    """Прочитать статус проекта; None — нет записи или БД недоступна."""
    try:
        async with AsyncSessionLocal() as session:
            row = await session.get(ProjectStatus, project_id)
            return row.status if row else None
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("Статус проекта %s не прочитан: %s", project_id, exc)
        return None
