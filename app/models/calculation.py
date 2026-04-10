"""
Модели для расчётов и коммерческих предложений.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from app.models.base import Base


class Calculation(Base):
    """
    Расчёт стоимости мебели для конкретного проекта.
    
    Один проект может иметь несколько вариантов расчётов (Вариант 1, Вариант 2 и т.д.)
    В расчёте хранятся:
    - Модули мебели (JSON)
    - Выбранные материалы (JSON)
    - Выбранная фурнитура (JSON)
    - Стекло (JSON)
    - Все итоговые цены
    - Скидки/наценки/бонусы
    """
    __tablename__ = "calculations"

    id = Column(Integer, primary_key=True, index=True)
    
    # Связь с проектом
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    project = relationship("Project", back_populates="calculations")
    
    # Имя варианта
    variant_name = Column(String(100), default="Вариант 1")  # "Вариант 1", "Вариант 2" и т.д.
    
    # ===== ВХОДНЫЕ ДАННЫЕ =====
    
    # Модули мебели (JSON)
    modules = Column(JSON, nullable=True)  # Список модулей с параметрами
    
    # Выбранные материалы
    selected_materials = Column(JSON, nullable=True)  # {
                                                      #   "ldsp": {"id": 1, "name": "EGGER", "price": 5600},
                                                      #   "facades": [...],
                                                      #   "edge": {...}
                                                      # }
    
    # Выбранная фурнитура
    selected_hardware = Column(JSON, nullable=True)  # {
                                                     #   "hinges": [...],
                                                     #   "drawers": [...],
                                                     #   "etc": [...]
                                                     # }
    
    # Стекло
    glass_items = Column(JSON, nullable=True)  # [{
                                               #   "type": "clear",
                                               #   "width_mm": 600,
                                               #   "height_mm": 800,
                                               #   "price": 5400,
                                               #   "tinting": false
                                               # }]
    
    # Дополнительные позиции
    countertops = Column(JSON, nullable=True)
    lighting = Column(JSON, nullable=True)
    accessories = Column(JSON, nullable=True)
    
    # ===== РАСЧЁТНЫЕ ДАННЫЕ (сметы по компонентам) =====
    
    # Себестоимость по компонентам
    material_cost = Column(Float, default=0)  # Материалы (ЛДСП, МДФ и т.д.)
    edge_cost = Column(Float, default=0)  # Кромка
    facade_cost = Column(Float, default=0)  # Фасады
    hardware_cost = Column(Float, default=0)  # Фурнитура (петли, ящики и т.д.)
    glass_cost = Column(Float, default=0)  # Стекло
    lighting_cost = Column(Float, default=0)  # Подсветка
    countertop_cost = Column(Float, default=0)  # Столешницы
    accessories_cost = Column(Float, default=0)  # Ручки, лотки и т.д.
    subcontractor_cost = Column(Float, default=0)  # От подрядчиков
    
    # Итоговая смета
    cost_price = Column(Float, default=0)  # Себестоимость
    manufacturing = Column(Float, default=0)  # Изготовление
    installation = Column(Float, default=0)  # Монтаж
    measurement_fee = Column(Float, default=2500)  # Замер
    delivery_fee = Column(Float, default=6500)  # Доставка (внутрь)
    delivery_fee_client = Column(Float, default=10000)  # Доставка (клиенту)
    design_fee = Column(Float, default=0)  # Проектировка
    overhead = Column(Float, default=0)  # Прочие расходы
    profit = Column(Float, default=0)  # Прибыль
    
    # Зарплаты
    tech_director_salary = Column(Float, default=0)  # Тех. директор
    manager_salary = Column(Float, default=0)  # Менеджер
    designer_salary = Column(Float, default=0)  # Дизайнер (ставка, не бонус)
    
    # Базовая цена (до скидок и бонусов)
    total_base_cash = Column(Float, default=0)  # Итого наличка (до скидок/наценок)
    
    # ===== СКИДКА / НАЦЕНКА (ЧАСТЬ 3) =====
    
    discount_type = Column(String(20), nullable=True)  # "percent" | "fixed" | None
    discount_value = Column(Float, default=0)  # Значение скидки (5 = 5% или 5000 = 5000₽)
    
    markup_type = Column(String(20), nullable=True)  # "percent" | "fixed" | None
    markup_value = Column(Float, default=0)  # Значение наценки
    
    # Бонус дизайнера
    designer_bonus_enabled = Column(Integer, default=0)  # 0/1
    designer_bonus_rate = Column(Float, default=0.10)  # 10% по умолчанию
    
    # ===== ФИНАЛЬНЫЕ ЦЕНЫ =====
    
    price_after_discount = Column(Float, nullable=True)  # Цена после скидки/наценки
    designer_bonus_amount = Column(Float, nullable=True)  # Сумма бонуса дизайнера
    final_price_cash = Column(Float, nullable=True)  # Финальная цена наличка
    final_price_noncash = Column(Float, nullable=True)  # Финальная цена безнал (×1.13)
    
    # ===== УПРАВЛЕНИЕ =====
    
    status = Column(String(50), default="draft")  # draft | sent | approved | archived
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связь с КП
    commercial_offers = relationship(
        "CommercialOffer",
        back_populates="calculation",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Calculation {self.variant_name} - {self.final_price_cash}₽>"


class CommercialOffer(Base):
    """
    Коммерческое предложение (КП) - PDF документ на основе расчёта.
    
    Из одного расчёта можно сгенерировать несколько вариантов КП
    (с разными скидками, вариантами и т.д.).
    """
    __tablename__ = "commercial_offers"

    id = Column(Integer, primary_key=True, index=True)
    
    # Связь с расчётом
    calculation_id = Column(Integer, ForeignKey("calculations.id"), nullable=False, index=True)
    calculation = relationship("Calculation", back_populates="commercial_offers")
    
    # Данные КП
    offer_number = Column(String(50), nullable=False, unique=True)  # Номер КП (КП-001, КП-002 и т.д.)
    pdf_filename = Column(String(255), nullable=True)  # Имя файла PDF
    pdf_path = Column(String(500), nullable=True)  # Путь к файлу в системе
    
    # Итоги на момент создания КП
    total_cash = Column(Float, nullable=False)  # Итого наличка
    total_noncash = Column(Float, nullable=False)  # Итого безнал
    
    # Управление
    status = Column(String(50), default="draft")  # draft | sent | approved | rejected | paid
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    sent_at = Column(DateTime, nullable=True)  # Когда отправлено клиенту
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<CommercialOffer {self.offer_number}>"
