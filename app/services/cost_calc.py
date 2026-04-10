"""
ПОЛНАЯ формула расчёта с скидками/наценками/бонусами.

Верифицировано на данных из прайса:
- Лист "Влад и Кристина": коэффициенты 0.80, 0.55, 0.10, 0.05, 0.30, 0.015, 1.13 ✓
- Лист "РАСЧЕТ ЗЕРКАЛ": бонус дизайнера = цена / (1 - 0.10) ✓
- Лист "СТОЛЕШНИЦЫ": та же формула бонуса ✓
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DiscountInfo:
    """Информация о скидке/наценке."""
    discount_type: Optional[str] = None   # "percent" | "fixed" | None
    discount_value: float = 0
    markup_type: Optional[str] = None     # "percent" | "fixed" | None
    markup_value: float = 0
    designer_bonus_enabled: bool = False
    designer_bonus_rate: float = 0.10     # 10% по умолчанию


@dataclass
class CostBreakdown:
    """Полная разбивка стоимости."""
    # === Входные данные (стоимость позиций) ===
    material_cost: float = 0        # ЛДСП, МДФ, ХДФ
    edge_cost: float = 0            # кромка
    facade_cost: float = 0          # фасады
    hardware_cost: float = 0        # фурнитура (петли, ящики, подъёмники, направляющие)
    glass_cost: float = 0           # стекло
    lighting_cost: float = 0        # подсветка
    countertop_cost: float = 0      # столешница + комплектующие
    accessories_cost: float = 0     # ручки, сушки, лотки, цоколь и т.д.
    subcontractor_cost: float = 0   # от подрядчиков

    # === Рассчитываемые поля ===
    cost_price: float = 0           # себестоимость (сумма всего)
    manufacturing: float = 0        # изготовление = себестоимость × 0.80
    installation: float = 0         # монтаж = себестоимость × 0.55
    measurement: float = 0          # замер (фикс.)
    delivery_internal: float = 0    # доставка (себестоимость)
    delivery_client: float = 0      # доставка (клиенту)
    design: float = 0               # проектировка = изготовление × 0.10
    overhead: float = 0             # прочие = себестоимость × 0.05
    subtotal: float = 0             # промежуточный итог
    profit: float = 0               # прибыль = итого × 0.30
    total_with_profit: float = 0    # итого с прибылью
    tech_director: float = 0        # тех. директор = (итого + прибыль) × 0.015
    manager_salary: float = 0       # менеджер
    designer_salary: float = 0      # дизайнер (фикс. ставка, не бонус)
    total_base_cash: float = 0      # итого наличка (до скидок)

    # === Скидка / наценка ===
    discount_amount: float = 0      # сумма скидки (отрицательная = наценка)
    price_after_discount: float = 0 # цена после скидки/наценки

    # === Бонус дизайнера ===
    designer_bonus_amount: float = 0  # сколько получит дизайнер
    final_cash: float = 0             # итого наличка ФИНАЛ

    # === Безнал ===
    final_noncash: float = 0          # итого безнал


def calculate_cost(
    # Стоимость позиций
    material_cost: float = 0,
    edge_cost: float = 0,
    facade_cost: float = 0,
    hardware_cost: float = 0,
    glass_cost: float = 0,
    lighting_cost: float = 0,
    countertop_cost: float = 0,
    accessories_cost: float = 0,
    subcontractor_cost: float = 0,
    # Коэффициенты
    manufacturing_rate: float = 0.80,
    installation_rate: float = 0.55,
    design_rate: float = 0.10,
    overhead_rate: float = 0.05,
    profit_rate: float = 0.30,
    tech_director_rate: float = 0.015,
    manager_rate: float = 0.0,
    designer_rate: float = 0.0,       # ставка дизайнера (не бонус!)
    measurement_fee: float = 2500,
    delivery_fee: float = 6500,
    delivery_fee_client: float = 10000,
    noncash_markup: float = 1.13,
    # Скидка / наценка / бонус
    discount: Optional[DiscountInfo] = None,
) -> CostBreakdown:
    """
    Полный расчёт стоимости заказа.

    Порядок:
    1. Себестоимость = сумма всех позиций
    2. Производные: изготовление, монтаж, проектировка, прочие
    3. Итого + прибыль + зарплаты
    4. Скидка или наценка
    5. Бонус дизайнера (если включён)
    6. Безнал
    """
    r = CostBreakdown(
        material_cost=material_cost,
        edge_cost=edge_cost,
        facade_cost=facade_cost,
        hardware_cost=hardware_cost,
        glass_cost=glass_cost,
        lighting_cost=lighting_cost,
        countertop_cost=countertop_cost,
        accessories_cost=accessories_cost,
        subcontractor_cost=subcontractor_cost,
    )

    disc = discount or DiscountInfo()

    # 1. Себестоимость
    r.cost_price = (
        material_cost + edge_cost + facade_cost + hardware_cost +
        glass_cost + lighting_cost + countertop_cost +
        accessories_cost + subcontractor_cost
    )

    # 2. Производные
    r.manufacturing = round(r.cost_price * manufacturing_rate)
    r.installation = round(r.cost_price * installation_rate)
    r.measurement = measurement_fee
    r.delivery_internal = delivery_fee
    r.delivery_client = delivery_fee_client
    r.design = round(r.manufacturing * design_rate)
    r.overhead = round(r.cost_price * overhead_rate)

    # 3. Итого
    r.subtotal = (
        r.cost_price + r.manufacturing + r.installation +
        r.measurement + r.delivery_internal +
        r.design + r.overhead
    )
    r.profit = round(r.subtotal * profit_rate)
    r.total_with_profit = r.subtotal + r.profit

    # Зарплаты
    r.tech_director = round(r.total_with_profit * tech_director_rate)
    r.manager_salary = round(r.total_with_profit * manager_rate)
    r.designer_salary = round(r.total_with_profit * designer_rate)

    r.total_base_cash = (
        r.total_with_profit + r.tech_director +
        r.manager_salary + r.designer_salary
    )

    # 4. Скидка или наценка (взаимоисключающие)
    r.price_after_discount = r.total_base_cash

    if disc.discount_type == "percent" and disc.discount_value > 0:
        r.discount_amount = round(r.total_base_cash * disc.discount_value / 100)
        r.price_after_discount = r.total_base_cash - r.discount_amount

    elif disc.discount_type == "fixed" and disc.discount_value > 0:
        r.discount_amount = disc.discount_value
        r.price_after_discount = r.total_base_cash - r.discount_amount

    elif disc.markup_type == "percent" and disc.markup_value > 0:
        markup = round(r.total_base_cash * disc.markup_value / 100)
        r.discount_amount = -markup  # отрицательная скидка = наценка
        r.price_after_discount = r.total_base_cash + markup

    elif disc.markup_type == "fixed" and disc.markup_value > 0:
        r.discount_amount = -disc.markup_value
        r.price_after_discount = r.total_base_cash + disc.markup_value

    # 5. Бонус дизайнера
    if disc.designer_bonus_enabled and disc.designer_bonus_rate > 0:
        # Формула: финал = цена / (1 - ставка)
        # Верифицировано: 26500 / 0.9 = 29444.44
        r.final_cash = round(
            r.price_after_discount / (1 - disc.designer_bonus_rate), 2
        )
        r.designer_bonus_amount = round(r.final_cash - r.price_after_discount, 2)
    else:
        r.final_cash = r.price_after_discount
        r.designer_bonus_amount = 0

    # 6. Безнал
    r.final_noncash = round(r.final_cash * noncash_markup, 2)

    return r