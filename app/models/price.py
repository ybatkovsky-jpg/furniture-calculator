"""
Модели для управления прайсом и историей цен.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from app.models.base import Base


class PriceItem(Base):
    """
    Позиция в прайсе.
    
    Поля для разных типов позиция (материалы, фурнитура, фасады и т.д.).
    """
    __tablename__ = "price_items"

    id = Column(Integer, primary_key=True, index=True)
    
    # Основные данные
    name = Column(String(255), nullable=False, index=True)  # Название позиции
    category = Column(String(100), nullable=False, index=True)  # Категория (ЛДСП, фасады и т.д.)
    subcategory = Column(String(100), nullable=True)  # Подкатегория
    brand = Column(String(100), nullable=True)  # Производитель
    
    # Цена
    unit_price = Column(Float, nullable=True)  # Цена за единицу
    price_unit = Column(String(50), default="шт")  # Единица измерения (шт, м, м², л и т.д.)
    
    # Флаг требования ручного ввода
    requires_manual_price = Column(Integer, default=0)  # 1 = оператор должен ввести цену
    
    # Дополнительная информация
    description = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)  # Доп. параметры (размеры, цвета и т.д.)
    
    # Управление
    is_active = Column(Integer, default=1)  # 1 = активна, 0 = архив
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связь с историей цен
    history = relationship("PriceHistory", back_populates="price_item", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<PriceItem {self.name} ({self.unit_price}₽)>"


class PriceHistory(Base):
    """
    История изменения цен позиций.
    
    Позволяет отслеживать все изменения цен для аудита и аналитики.
    """
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, index=True)
    
    # Связь с позицией прайса
    item_id = Column(Integer, ForeignKey("price_items.id"), nullable=False, index=True)
    price_item = relationship("PriceItem", back_populates="history")
    
    # История цен
    old_price = Column(Float, nullable=True)  # Старая цена
    new_price = Column(Float, nullable=False)  # Новая цена
    
    # Метаданные
    changed_by = Column(String(100), nullable=True)  # Кто изменил (логин оператора)
    change_reason = Column(String(255), nullable=True)  # Причина изменения
    changed_at = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<PriceHistory {self.item_id}: {self.old_price}→{self.new_price}>"
