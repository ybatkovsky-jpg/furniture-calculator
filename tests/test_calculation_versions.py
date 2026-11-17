"""P1-10: версии расчёта — нумерация, неизменяемость, кто и почему.

`Calculation.quote` — последний снимок; `CalculationVersion` — история.
Пересчёт/скидка/бонус создают новую версию, старая не переписывается.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Calculation, CalculationVersion, PriceItem, Project
from app.services.calculation_service import CalculationService


async def _session_with_calc():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    session.add_all([
        PriceItem(name="ХДФ 3мм", category="ХДФ", unit_price=1530,
                  price_unit="лист", is_active=1),
        PriceItem(name="Конфирмат 7×50", category="Крепёж", unit_price=2,
                  price_unit="шт", is_active=1),
        PriceItem(name="Регулируемая опора", category="Крепёж", unit_price=50,
                  price_unit="шт", is_active=1),
        PriceItem(name="Настенный подвес", category="Крепёж", unit_price=120,
                  price_unit="шт", is_active=1),
        PriceItem(name="Цоколь ПВХ Rehau 4м", category="Крепёж", unit_price=1500,
                  price_unit="шт", is_active=1),
    ])
    project = Project(telegram_user_id="1", project_name="Т",
                      client_name="К", client_phone="+7")
    session.add(project)
    await session.flush()
    calc = Calculation(
        project_id=project.id,
        modules=[{"type": "lower_base", "width": 600, "depth": 560,
                  "height": 820, "facades": {"count": 1, "type": "doors"}}],
        selected_materials={
            "ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
            "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000,
                        "price_unit": "м²"},
            "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34}],
        },
        selected_hardware={"hinges": {"price": 200, "brand": "FIRMAX"},
                           "handles": {"price": 500}},
        zone_type="Гостиная",
    )
    session.add(calc)
    await session.commit()
    return engine, session, calc


def _run(scenario):
    async def wrapper():
        engine, session, calc = await _session_with_calc()
        try:
            await scenario(session, calc)
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(wrapper())


async def _versions(session, calculation_id):
    result = await session.execute(
        select(CalculationVersion)
        .where(CalculationVersion.calculation_id == calculation_id)
        .order_by(CalculationVersion.version_no)
    )
    return list(result.scalars().all())


def test_versions_are_numbered_without_gaps():
    async def scenario(session, calculation):
        service = CalculationService(session)
        await service.recalculate(calculation, reason="initial", user_id=42)
        await service.apply_discount(
            calculation, discount_type="percent", discount_value=10, user_id=42
        )
        await service.clear_discount(calculation, user_id=43)

        versions = await _versions(session, calculation.id)
        assert [v.version_no for v in versions] == [1, 2, 3]
        assert [v.reason for v in versions] == ["initial", "discount", "discount_removed"]
        assert versions[0].created_by == 42
        assert versions[-1].created_by == 43
        assert versions[0].price_list_version
        assert versions[0].policy_version == "1.0"

    _run(scenario)


def test_old_version_is_not_rewritten():
    async def scenario(session, calculation):
        service = CalculationService(session)
        await service.recalculate(calculation, reason="initial")
        first_final = calculation.final_price_cash

        await service.apply_discount(
            calculation, discount_type="percent", discount_value=10
        )
        versions = await _versions(session, calculation.id)
        assert len(versions) == 2
        assert versions[0].quote["totals"]["final_cash"] == first_final
        assert versions[0].quote["totals"]["discount_amount"] == 0
        assert versions[1].quote["totals"]["discount_amount"] > 0
        assert versions[1].quote["reason"] == "discount"

    _run(scenario)


def test_quote_keeps_last_snapshot_and_versions_keep_all():
    async def scenario(session, calculation):
        service = CalculationService(session)
        await service.recalculate(calculation, reason="initial")
        quote_v1 = dict(calculation.quote)
        await service.set_designer_bonus(calculation, True)

        versions = await _versions(session, calculation.id)
        assert len(versions) == 2
        assert quote_v1["totals"]["final_cash"] != calculation.quote["totals"]["final_cash"]
        assert versions[0].quote["totals"]["final_cash"] == quote_v1["totals"]["final_cash"]

    _run(scenario)
