"""P1-09/P1-01: единая политика коэффициентов и финансовая секция Excel."""

import asyncio
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base, CalcSettings
from app.services.calc_engine import calculate_full_cost
from app.services.cost_calc import calculate_cost
from app.services.pricing_policy import (
    PricingPolicy,
    load_default_policy,
)
from app.services.template_filler import _apply_pricing_policy

ROOT = Path(__file__).resolve().parents[1]


def test_default_policy_matches_config():
    policy = PricingPolicy.from_config()
    assert policy.manufacturing_rate == settings.manufacturing_rate
    assert policy.installation_rate == settings.installation_rate
    assert policy.design_rate == settings.design_rate
    assert policy.profit_rate == settings.profit_rate
    assert policy.designer_bonus_rate == settings.designer_bonus_rate


def test_calc_settings_overrides_config():
    row = CalcSettings(
        name="Тест",
        manufacturing_rate=0.5,
        design_rate=0.2,
        profit_rate=0.1,
        delivery_fee=1000,
    )
    policy = PricingPolicy.from_calc_settings(row)
    assert policy.manufacturing_rate == 0.5
    assert policy.design_rate == 0.2
    assert policy.profit_rate == 0.1
    assert policy.delivery_fee == 1000
    # Незаполненные поля — из config.
    assert policy.installation_rate == settings.installation_rate


def test_load_default_policy_from_db():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            fallback = await load_default_policy(session)
            assert fallback.manufacturing_rate == settings.manufacturing_rate

            session.add(
                CalcSettings(
                    name="Свой",
                    is_default=1,
                    is_active=1,
                    manufacturing_rate=0.6,
                    delivery_fee=1234,
                )
            )
            await session.commit()
            policy = await load_default_policy(session)
            assert policy.manufacturing_rate == 0.6
            assert policy.delivery_fee == 1234
        await engine.dispose()

    asyncio.run(scenario())


def test_calc_engine_uses_policy():
    module = {
        "type": "lower_base", "width": 600, "depth": 560, "height": 820,
        "quantity": 1, "facades": {"count": 1}, "shelves": 1,
    }
    materials = {
        "ldsp": {"price": 6000, "price_unit": "лист"},
        "facades": {"price": 5000, "price_unit": "м²"},
        "edge": {"price": 34, "thickness_mm": 0.8},
    }
    base = calculate_full_cost(
        [module], materials, {"hinges": {"price": 200}}, [],
        zone_type="Гостиная",
        price_book={"hdf": 1000},
    )
    cheap = calculate_full_cost(
        [module], materials, {"hinges": {"price": 200}}, [],
        zone_type="Гостиная",
        price_book={"hdf": 1000},
        settings=PricingPolicy(profit_rate=0.0, design_rate=0.0),
    )
    assert cheap.total_cash < base.total_cash


def test_excel_financial_section_uses_same_policy():
    """Финансовая секция Excel переписывается под policy, без двойного SUM."""
    policy = PricingPolicy(
        manufacturing_rate=0.8,
        installation_rate=0.55,
        design_rate=0.10,
        overhead_rate=0.05,
        profit_rate=0.30,
        tech_director_rate=0.015,
        manager_rate=0.0,
        delivery_fee=6500,
        measurement_fee=2500,
        noncash_markup=1.13,
    )
    wb = load_workbook(ROOT / "templates" / "Таблица для расчетов пустая.xlsx")
    ws = wb["Рассчет"]
    ws["F3"] = 100_000  # имитируем себестоимость из строк
    assert _apply_pricing_policy(ws, policy) is True

    # P2-16: в Excel пишутся значения, а не формулы.
    # День 4: доставка клиенту 10 000 ₽ — отдельная строка, в базу B и
    # наценку 30% не входит.
    assert ws["E347"].value == 100_000
    assert ws["E348"].value == 80_000      # изготовление 0.80
    assert ws["E349"].value == 55_000      # монтаж 0.55
    assert ws["E351"].value == 10_000      # доставка клиенту
    assert ws["E352"].value == 8_000       # проектировка 0.10 от изготовления
    assert ws["E353"].value == 5_000       # прочие 0.05
    assert ws["E354"].value == 250_500     # промежуточный итог без доставки
    assert ws["E355"].value == 75_150      # прибыль 0.30
    assert ws["E359"].value == 340_535     # наличка: мебель + доставка
    assert ws["E360"].value == 383_504.55  # безнал: мебель×1.13 + доставка


def test_excel_values_are_readable_without_recalc(tmp_path):
    """P2-16: data_only=True видит итог без открытия Excel."""
    wb = load_workbook(ROOT / "templates" / "Таблица для расчетов пустая.xlsx")
    ws = wb["Рассчет"]
    ws["F3"] = 50_000
    assert _apply_pricing_policy(ws, PricingPolicy.from_config()) is True
    path = tmp_path / "quote.xlsx"
    wb.save(path)

    reread = load_workbook(path, data_only=True)["Рассчет"]
    assert reread["E359"].value and reread["E359"].value > 0
    furniture_cash = reread["E359"].value - 10_000
    # Наценку берём из политики, а не числом: иначе тест зависит от .env.
    policy = PricingPolicy.from_config()
    assert reread["E360"].value == round(furniture_cash * policy.noncash_markup + 10_000, 2)


def test_golden_cost_matches_excel_formula_chain():
    """При одинаковой себестоимости формулы Excel дают тот же итог, что чат."""
    policy = PricingPolicy.from_config()
    cost = 100_000.0
    result = calculate_cost(
        material_cost=cost,
        **policy.as_cost_kwargs(),
    )
    # Повторяем формулы Excel на Python. Доставка клиенту — отдельно и без
    # наценки 30%; безналичная наценка 13% — на мебель, не на доставку.
    manufacturing = round(cost * policy.manufacturing_rate)
    measurement = policy.measurement_fee
    delivery = policy.delivery_fee_client
    design = round(manufacturing * policy.design_rate)
    overhead = round(cost * policy.overhead_rate)
    # Монтаж с 19.11 — 15 % от конечной цены клиента: он входит в базу и на него
    # начисляются прибыль и зарплаты, поэтому считаем из уравнения.
    base_without_install = round(sum([cost, manufacturing, measurement, design, overhead]))
    markup = (
        (1 + policy.profit_rate)
        * (1 + policy.tech_director_rate + policy.manager_rate + policy.designer_rate)
    )
    share = policy.installation_rate * markup
    installation = round(base_without_install * share / (1 - share))
    subtotal = round(base_without_install + installation)
    profit = round(subtotal * policy.profit_rate)
    total_with_profit = round(profit + subtotal)
    tech = round(total_with_profit * policy.tech_director_rate)
    furniture_cash = round(total_with_profit + tech)
    cash = round(furniture_cash + delivery)
    noncash = round(furniture_cash * policy.noncash_markup + delivery, 2)

    assert result.manufacturing == manufacturing
    assert result.installation == installation
    assert result.design == design
    assert result.overhead == overhead
    assert result.profit == profit
    assert result.tech_director == tech
    assert result.total_base_cash == furniture_cash
    assert result.final_cash == cash
    assert result.final_noncash == noncash
