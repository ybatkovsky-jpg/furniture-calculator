"""Статус проекта импорта в БД (этап 1D).

Манифест `project.json` хранит статус рядом с файлами, но для списка проектов
и истории нужен отдельный запрос по БД, не читающий каждый манифест.
"""

from sqlalchemy import Column, DateTime, String

from app.db.time import utc_now
from app.models.base import Base


class ProjectStatus(Base):
    """Текущий статус импортированного проекта."""

    __tablename__ = "project_statuses"

    project_id = Column(String(16), primary_key=True)
    status = Column(String(32), nullable=False, default="IMPORTED")
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    def __repr__(self) -> str:
        return f"<ProjectStatus {self.project_id}: {self.status}>"
