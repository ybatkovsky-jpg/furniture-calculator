"""Проверка прав доступа к проектам и расчётам (P1-14).

Проблема аудита: обработчики загружали расчёт по `id` без сверки владельца,
поэтому любой, кто знал/угадал `calculation_id`, мог открыть чужую смету,
скачать КП, поменять скидку или создать вариант. Поле
`Project.telegram_user_id` существовало, но не использовалось.

Правило: все обращения к расчёту из бота идут через
`get_owned_calculation()` / `get_owned_project()`. Администратор — это
отдельный явный флаг у вызывающего кода, а не «все видят всё»: функции
ниже по умолчанию проверяют только владельца.
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.calculation import Calculation
from app.models.project import Project


async def get_owned_calculation(
    session: AsyncSession,
    calculation_id: int,
    user_id: int | str,
    *,
    with_project: bool = False,
) -> Optional[Calculation]:
    """Вернуть расчёт, только если он принадлежит пользователю.

    `None` означает «нет такого расчёта ИЛИ он чужой» — вызывающий код
    обязан ответить одинаково в обоих случаях, чтобы не подтверждать
    существование чужих id.
    """
    stmt = (
        select(Calculation)
        .join(Project, Calculation.project_id == Project.id)
        .where(
            Calculation.id == calculation_id,
            Project.telegram_user_id == str(user_id),
        )
    )
    if with_project:
        stmt = stmt.options(selectinload(Calculation.project))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_owned_project(
    session: AsyncSession,
    project_id: int,
    user_id: int | str,
) -> Optional[Project]:
    """Вернуть проект, только если он принадлежит пользователю."""
    stmt = select(Project).where(
        Project.id == project_id,
        Project.telegram_user_id == str(user_id),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def is_owner(project: Optional[Project], user_id: int | str) -> bool:
    """Синхронная проверка владельца уже загруженного проекта."""
    if project is None:
        return False
    return str(project.telegram_user_id) == str(user_id)
