"""
Модель для проектов клиентов.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import Base


class Project(Base):
    """
    Проект клиента - контейнер для всех расчётов и данных клиента.
    
    Один проект может содержать несколько вариантов расчётов (Calculation).
    """
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    
    # Идентификация
    telegram_user_id = Column(String(50), nullable=False, index=True)  # ID пользователя в Telegram
    project_name = Column(String(255), nullable=True)  # Название проекта (например "Кухня Влада")
    
    # Данные клиента
    client_name = Column(String(255), nullable=False)  # ФИО клиента
    client_phone = Column(String(20), nullable=False)  # Телефон клиента
    client_address = Column(Text, nullable=True)  # Адрес объекта
    
    # Статус
    status = Column(String(50), default="draft")  # draft | active | completed | archived
    
    # История
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связь с расчётами
    calculations = relationship(
        "Calculation",
        back_populates="project",
        cascade="all, delete-orphan"
    )

    # Свойство для получения последнего расчёта (P2-19).
    @property
    def latest_calculation(self):
        """Последний расчёт проекта (по id; created_at может быть None)."""
        if not self.calculations:
            return None
        return max(self.calculations, key=lambda c: (c.id or 0))

    @property
    def calculation(self):
        """Совместимость: «текущий» расчёт = последний, а не первый."""
        return self.latest_calculation

    def __repr__(self):
        return f"<Project {self.client_name} - {self.project_name}>"
