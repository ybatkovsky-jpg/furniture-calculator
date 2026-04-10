"""
Базовая конфигурация SQLAlchemy моделей.
"""

from sqlalchemy.orm import DeclarativeBase
from datetime import datetime
from sqlalchemy import DateTime, func


class Base(DeclarativeBase):
    """Базовый класс для всех моделей SQLAlchemy."""
    pass
