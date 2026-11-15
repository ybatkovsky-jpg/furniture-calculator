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
    assert _apply_pricing_policy(ws, policy) is True

    # Строка себестоимости не должна ссылаться сама на себя.
    cost_value = ws["E347"].value
    assert "SUM(F3:F345)" in cost_value
    # Коэффициенты — из policy.
    assert "0.8*E347" in ws["E348"].value
    assert "0.55*E347" in ws["E349"].value
    assert "0.1*E348" in ws["E352"].value
    assert "0.05*E347" in ws["E353"].value
    assert "0.3*E354" in ws["E355"].value
    # Промежуточный итог — SUM от себестоимости до прочих, без прибыли.
    assert ws["E354"].value == "=ROUND(SUM(E347:E353),0)"
    assert ws["E356"].value == "=ROUND(E355+E354,0)"


def test_golden_cost_matches_excel_formula_chain():
    """При одинаковой себестоимости формулы Excel дают тот же итог, что чат."""
    policy = PricingPolicy.from_config()
    cost = 100_000.0
    result = calculate_cost(
        material_cost=cost,
        **policy.as_cost_kwargs(),
    )
    # Повторяем формулы Excel на Python.
    manufacturing = round(cost * policy.manufacturing_rate)
    installation = round(cost * policy.installation_rate)
    measurement = policy.measurement_fee
    delivery = policy.delivery_fee
    design = round(manufacturing * policy.design_rate)
    overhead = round(cost * policy.overhead_rate)
    subtotal = round(sum([cost, manufacturing, installation, measurement, delivery, design, overhead]))
    profit = round(subtotal * policy.profit_rate)
    total_with_profit = round(profit + subtotal)
    tech = round(total_with_profit * policy.tech_director_rate)
    cash = round(total_with_profit + tech)
    noncash = round(cash * policy.noncash_markup, 2)

    assert result.manufacturing == manufacturing
    assert result.installation == installation
    assert result.design == design
    assert result.overhead == overhead
    assert result.profit == profit
    assert result.tech_director == tech
    assert result.total_base_cash == cash
    assert result.final_noncash == noncash
