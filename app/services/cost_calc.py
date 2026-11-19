"""
ПОЛНАЯ формула расчёта с скидками/наценками/бонусами.

Верифицировано на данных из прайса:
- Лист "Влад и Кристина": коэффициенты 0.80, 0.55, 0.10, 0.05, 0.30, 0.015, 1.10 ✓
- Лист "РАСЧЕТ ЗЕРКАЛ": бонус дизайнера = цена / (1 - 0.10) ✓
- Лист "СТОЛЕШНИЦЫ": та же формула бонуса ✓
"""

from dataclasses import dataclass, field

from app.domain.money import money as _money
from app.domain.money import rub as _rub
from app.domain.validation import (
    PricingError,
    validate_cost_components,
    validate_discount_against_base,
    validate_discount_inputs,
)


def _round_half_up(value: float, digits: int = 0) -> float:
    """Округление денег HALF_UP (round() в Python — банковское, P1-24)."""
    if digits == 0:
        return float(_rub(value))
    return float(_money(value))


@dataclass
class DiscountInfo:
    """Информация о скидке/наценке."""
    discount_type: str | None = None   # "percent" | "fixed" | None
    discount_value: float = 0
    markup_type: str | None = None     # "percent" | "fixed" | None
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
    fastener_cost: float = 0        # конфирмат, опоры, подвесы, цоколь (P0-02)

    # === Рассчитываемые поля ===
    cost_price: float = 0           # закупка материалов и фурнитуры (НЕ полная себестоимость)
    manufacturing: float = 0        # изготовление = себестоимость × 0.80
    installation: float = 0         # монтаж = 15 % от конечной цены клиента
    measurement: float = 0          # замер (фикс.)
    delivery_internal: float = 0    # доставка (себестоимость)
    delivery_client: float = 0      # доставка (клиенту)
    design: float = 0               # проектировка = изготовление × 0.10
    overhead: float = 0             # прочие = себестоимость × 0.05
    risk_reserve: float = 0         # запас на возможные ошибки
    subtotal: float = 0             # полная себестоимость (закупка + работы + накладные)
    profit: float = 0               # прибыль = итого × 0.35
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

    # === Налоги (УСН 15%, НДС 0) — управленческий результат, не цена ===
    tax_base: float = 0
    tax_usn: float = 0
    min_tax: float = 0
    tax_accrual: float = 0
    vat: float = 0
    net_profit: float = 0

    @property
    def full_cost(self) -> float:
        """Полная себестоимость: закупка + работы + накладные (без прибыли).

        `cost_price` — это только закупка материалов и фурнитуры; путать нельзя.
        """
        return self.subtotal

    # === Предупреждения валидатора ===
    warnings: list[str] = field(default_factory=list)

    # === P1-02: цена ниже полных затрат ===
    below_cost: bool = False
    below_cost_gap: float = 0
    furniture_cash: float = 0     # цена мебели без клиентской доставки


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
    fastener_cost: float = 0,
    # Коэффициенты
    manufacturing_rate: float = 0.80,
    installation_rate: float = 0.15,
    design_rate: float = 0.10,
    overhead_rate: float = 0.05,
    profit_rate: float = 0.35,
    risk_reserve_rate: float = 0.0,
    tech_director_rate: float = 0.015,
    manager_rate: float = 0.0,
    designer_rate: float = 0.0,       # ставка дизайнера (не бонус!)
    measurement_fee: float = 2500,
    delivery_fee: float = 6500,
    delivery_fee_client: float = 10000,
    noncash_markup: float = 1.10,
    # P1-02: минимальная маржа над полными затратами (0 = не ниже затрат).
    min_margin_rate: float = 0.0,
    # Скидка / наценка / бонус
    discount: DiscountInfo | None = None,
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
    # Проверяем и исправляем None значения на 0
    material_cost = material_cost if material_cost is not None else 0
    edge_cost = edge_cost if edge_cost is not None else 0
    facade_cost = facade_cost if facade_cost is not None else 0
    hardware_cost = hardware_cost if hardware_cost is not None else 0
    glass_cost = glass_cost if glass_cost is not None else 0
    lighting_cost = lighting_cost if lighting_cost is not None else 0
    countertop_cost = countertop_cost if countertop_cost is not None else 0
    accessories_cost = accessories_cost if accessories_cost is not None else 0
    subcontractor_cost = subcontractor_cost if subcontractor_cost is not None else 0
    fastener_cost = fastener_cost if fastener_cost is not None else 0

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
        fastener_cost=fastener_cost,
    )

    disc = discount or DiscountInfo()

    # 0. Валидация входов (P0-08): отрицательные стоимости, проценты > 100,
    # одновременная скидка и наценка, ставка бонуса ≥ 1 — ошибка, а не
    # отрицательный итог/падение.
    validate_cost_components(
        [
            ("Материалы", material_cost),
            ("Кромка", edge_cost),
            ("Фасады", facade_cost),
            ("Фурнитура", hardware_cost),
            ("Стекло", glass_cost),
            ("Подсветка", lighting_cost),
            ("Столешница", countertop_cost),
            ("Комплектующие", accessories_cost),
            ("Подрядчики", subcontractor_cost),
            ("Крепёж", fastener_cost),
        ]
    )
    validate_discount_inputs(
        disc.discount_type,
        disc.discount_value,
        disc.markup_type,
        disc.markup_value,
        disc.designer_bonus_rate,
    )

    # 1. Себестоимость
    r.cost_price = (
        material_cost + edge_cost + facade_cost + hardware_cost +
        glass_cost + lighting_cost + countertop_cost +
        accessories_cost + subcontractor_cost + fastener_cost
    )

    # 2. Производные
    r.manufacturing = _round_half_up(r.cost_price * manufacturing_rate)
    r.measurement = measurement_fee
    r.delivery_internal = delivery_fee
    r.delivery_client = delivery_fee_client
    r.design = _round_half_up(r.manufacturing * design_rate)
    r.overhead = _round_half_up(r.cost_price * overhead_rate)
    # Небольшой запас на ошибки распознавания/состава (решение 18.11).
    r.risk_reserve = _round_half_up(r.cost_price * risk_reserve_rate)

    # Монтаж — 15 % от конечной цены клиента (решение 19.11). Он входит в базу,
    # на него начисляются прибыль и зарплаты, поэтому считаем его из уравнения:
    #   install = rate × (A + install) × K, где K — все наценки поверх базы,
    #   A — база без монтажа. Отсюда install = rate × K × A / (1 − rate × K).
    base_without_install = (
        r.cost_price + r.manufacturing + r.measurement +
        r.design + r.overhead + r.risk_reserve
    )
    markup = (
        (1 + profit_rate)
        * (1 + tech_director_rate + manager_rate + designer_rate)
    )
    share = installation_rate * markup
    r.installation = _round_half_up(
        base_without_install * share / (1 - share) if share < 1 else 0.0
    )

    # 3. Итого. Внутренняя доставка не входит в расчётную базу B: по
    # финансовой политике §6 она учитывается отдельно как расход, а клиенту
    # предъявляется D_client = 10 000 ₽ отдельной строкой (без наценки 30%).
    r.subtotal = (
        r.cost_price + r.manufacturing + r.installation +
        r.measurement + r.design + r.overhead + r.risk_reserve
    )
    r.profit = _round_half_up(r.subtotal * profit_rate)
    r.total_with_profit = r.subtotal + r.profit

    # Зарплаты
    r.tech_director = _round_half_up(r.total_with_profit * tech_director_rate)
    r.manager_salary = _round_half_up(r.total_with_profit * manager_rate)
    r.designer_salary = _round_half_up(r.total_with_profit * designer_rate)

    r.total_base_cash = (
        r.total_with_profit + r.tech_director +
        r.manager_salary + r.designer_salary
    )

    # 4. Скидка или наценка (взаимоисключающие)
    validate_discount_against_base(
        disc.discount_type,
        disc.discount_value,
        disc.markup_type,
        disc.markup_value,
        r.total_base_cash,
    )
    r.price_after_discount = r.total_base_cash

    if disc.discount_type == "percent" and disc.discount_value > 0:
        r.discount_amount = _round_half_up(
            r.total_base_cash * disc.discount_value / 100
        )
        r.price_after_discount = r.total_base_cash - r.discount_amount

    elif disc.discount_type == "fixed" and disc.discount_value > 0:
        r.discount_amount = disc.discount_value
        r.price_after_discount = r.total_base_cash - r.discount_amount

    elif disc.markup_type == "percent" and disc.markup_value > 0:
        markup = _round_half_up(r.total_base_cash * disc.markup_value / 100)
        r.discount_amount = -markup  # отрицательная скидка = наценка
        r.price_after_discount = r.total_base_cash + markup

    elif disc.markup_type == "fixed" and disc.markup_value > 0:
        r.discount_amount = -disc.markup_value
        r.price_after_discount = r.total_base_cash + disc.markup_value

    # 5. Бонус дизайнера
    if disc.designer_bonus_enabled and disc.designer_bonus_rate > 0:
        # Формула: финал = цена / (1 - ставка)
        # Верифицировано: 26500 / 0.9 = 29444.44
        furniture_cash = _round_half_up(
            r.price_after_discount / (1 - disc.designer_bonus_rate), 2
        )
        r.designer_bonus_amount = _round_half_up(
            furniture_cash - r.price_after_discount, 2
        )
    else:
        furniture_cash = r.price_after_discount
        r.designer_bonus_amount = 0

    # 6. Клиентская доставка — отдельной строкой, без наценки 30% и без
    # безналичной наценки: это не мебель и не налог (политика §6).
    r.furniture_cash = _round_half_up(furniture_cash, 2)
    r.final_cash = _round_half_up(furniture_cash + r.delivery_client, 2)
    r.final_noncash = _round_half_up(
        furniture_cash * noncash_markup + r.delivery_client, 2
    )

    # P1-02: защита от убытка по ПОЛНЫМ затратам. Раньше сравнивали
    # final_cash (с доставкой 10 000) с cost_price (только материалы):
    # скидка 50 % ниже полных затрат на 14 261 ₽, а warnings был пуст.
    min_price = _round_half_up(r.subtotal * (1 + float(min_margin_rate or 0)))
    if r.furniture_cash < min_price:
        r.below_cost = True
        r.below_cost_gap = _round_half_up(min_price - r.furniture_cash)
        r.warnings.append(
            "Цена мебели ниже полных затрат на "
            f"{r.below_cost_gap:,.0f} ₽: выпуск КП требует подтверждения директора"
        )

    # 7. УСН 15% от прибыли; НДС 0. На цену клиенту не влияет.
    from app.services.tax_policy import TaxPolicy

    tax = TaxPolicy.from_config().calculate(
        revenue=r.final_cash,
        deductible_expenses=r.cost_price + r.delivery_internal,
    )
    r.tax_base = tax.tax_base
    r.tax_usn = tax.tax_usn
    r.min_tax = tax.min_tax
    r.tax_accrual = tax.tax_accrual
    r.vat = tax.vat
    r.net_profit = tax.net_profit

    # 8. Жёсткий инвариант: итог не может быть отрицательным. Проверка «ниже
    # затрат» — выше (P1-02), она сравнивает цену мебели без доставки с
    # полными затратами, а не с материалами.
    if r.final_cash < 0:
        raise PricingError("Итоговая цена отрицательна — проверьте скидку")

    return r