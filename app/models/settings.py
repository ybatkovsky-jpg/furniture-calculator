"""
Модель для настроек расчётов.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, JSON
from datetime import datetime
from app.models.base import Base


class CalcSettings(Base):
    """
    Глобальные настройки коэффициентов расчёта мебели.
    
    Эти коэффициенты используются при расчёте стоимости.
    Они могут быть переопределены оператором для конкретного расчёта.
    
    Из прайса CURSOR_INSTRUCTIONS_v2.md:
    - Изготовление: ×0.80
    - Монтаж: ×0.55
    - Замер: 2 500 ₽ (фикс.)
    - Доставка внутрь: 6 500 ₽ (фикс.)
    - Доставка клиенту: 10 000 ₽ (фикс.)
    - Проектировка: ×0.10 от изготовления
    - Прочие: ×0.05
    - Прибыль: ×0.30
    - Тех. директор: ×0.015
    - Бонус дизайнера: по умолчанию 10%
    - Коэфф. безнала: ×1.13
    - Коэфф. использования листа: 0.85
    """
    __tablename__ = "calc_settings"

    id = Column(Integer, primary_key=True, index=True)
    
    # Название профиля настроек
    name = Column(String(100), unique=True, nullable=False)  # "Стандартные", "Эконом", "Люкс" и т.д.
    description = Column(String(255), nullable=True)
    
    # ===== КОЭФФИЦИЕНТЫ РАСЧЁТА =====
    
    # Производство
    manufacturing_rate = Column(Float, default=0.80)  # Изготовление
    installation_rate = Column(Float, default=0.55)  # Монтаж
    design_rate = Column(Float, default=0.10)  # Проектировка (от изготовления)
    overhead_rate = Column(Float, default=0.05)  # Прочие расходы
    
    # Прибыль и зарплаты
    profit_rate = Column(Float, default=0.30)  # Прибыль
    tech_director_rate = Column(Float, default=0.015)  # Тех. директор (% от итого)
    manager_rate = Column(Float, default=0.0)  # Менеджер (% от итого)
    designer_rate = Column(Float, default=0.0)  # Дизайнер ставка (% от итого, не бонус!)
    
    # Фиксированные сборы
    measurement_fee = Column(Float, default=2500)  # Замер
    delivery_fee = Column(Float, default=6500)  # Доставка внутрь
    delivery_fee_client = Column(Float, default=10000)  # Доставка клиенту
    
    # Прочие коэффициенты
    noncash_markup = Column(Float, default=1.13)  # Наценка за безнал
    sheet_utilization = Column(Float, default=0.85)  # Коэфф. использования листа ЛДСП/МДФ
    
    # Бонус дизайнера
    designer_bonus_rate = Column(Float, default=0.10)  # 10% объёма от финальной цены
    
    # ===== УПРАВЛЕНИЕ =====
    
    is_default = Column(Integer, default=0)  # 1 = используется по умолчанию
    is_active = Column(Integer, default=1)  # 1 = активен, 0 = архив
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<CalcSettings {self.name}>"

    def to_dict(self):
        """Вернуть все коэффициенты в виде словаря."""
        return {
            "id": self.id,
            "name": self.name,
            "manufacturing_rate": self.manufacturing_rate,
            "installation_rate": self.installation_rate,
            "design_rate": self.design_rate,
            "overhead_rate": self.overhead_rate,
            "profit_rate": self.profit_rate,
            "tech_director_rate": self.tech_director_rate,
            "manager_rate": self.manager_rate,
            "designer_rate": self.designer_rate,
            "measurement_fee": self.measurement_fee,
            "delivery_fee": self.delivery_fee,
            "delivery_fee_client": self.delivery_fee_client,
            "noncash_markup": self.noncash_markup,
            "sheet_utilization": self.sheet_utilization,
            "designer_bonus_rate": self.designer_bonus_rate,
        }
