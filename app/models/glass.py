"""
Модель для типов стекла и их характеристик.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, JSON
from datetime import datetime
from app.models.base import Base


class GlassType(Base):
    """
    Тип стекла доступного в каталоге.
    
    Примеры:
    - Обычное (clear)
    - Рифленое MORU (moru_riflenoe)
    - Графит (graphite)
    - Бронза (bronze)
    - Для алюминиевого профиля (for_aluminum)
    """
    __tablename__ = "glass_types"

    id = Column(Integer, primary_key=True, index=True)
    
    # Основные данные
    name = Column(String(100), nullable=False, unique=True, index=True)  # Название (Обычное, Графит и т.д.)
    code = Column(String(50), nullable=False, unique=True)  # Код для системы (clear, graphite и т.д.)
    
    # Цена (может быть пустой для типов с ручным вводом)
    price_per_sqm = Column(Float, nullable=True)  # Цена за м²
    price_per_unit = Column(Float, nullable=True)  # Цена за единицу (например лист)
    unit_name = Column(String(50), nullable=True)  # Название единицы (шт, лист, м² и т.д.)
    
    # Логика расчёта
    requires_manual_price = Column(Integer, default=0)  # 1 = необходимо вводить цену вручную
    calculation_type = Column(String(50), default="sqm")  # sqm (м²) | sheet (листы) | unit (единицы)
    
    # Параметры
    sheet_width_mm = Column(Integer, nullable=True)  # Ширина листа (мм)
    sheet_height_mm = Column(Integer, nullable=True)  # Высота листа (мм)
    tinting_available = Column(Integer, default=0)  # 1 = доступна тонировка
    tinting_price = Column(Float, nullable=True)  # Цена за тонировку
    
    # Опции стекла
    options_json = Column(JSON, nullable=True)  # Доп. опции в JSON
    
    # Управление
    is_active = Column(Integer, default=1)  # 1 = активен, 0 = архив
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<GlassType {self.name}>"
