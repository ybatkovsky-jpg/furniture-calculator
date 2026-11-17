"""P1-10 (срез): сохранённый расчёт не меняется от нового прайса.

Снимок `quote` хранит `price_book` и коэффициенты. Обычное открытие/скидка
пересчитывают по снимку; текущий прайс — только явный `price_mode="current"`.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, CalcSettings, Calculation, PriceItem, Project
from app.services.calculation_service import CalculationService


def _run(scenario):
    async def wrapper():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            hdf = PriceItem(name="ХДФ 3мм", category="ХДФ", unit_price=1530,
                            price_unit="лист", is_active=1)
            session.add_all([
                hdf,
                PriceItem(name="Конфирмат 7×50", category="Крепёж", unit_price=2,
                          price_unit="шт", is_active=1),
                PriceItem(name="Регулируемая опора", category="Крепёж", unit_price=50,
                          price_unit="шт", is_active=1),
                PriceItem(name="Настенный подвес", category="Крепёж", unit_price=120,
                          price_unit="шт", is_active=1),
                PriceItem(name="Цоколь ПВХ Rehau 4м", category="Крепёж",
                          unit_price=1500, price_unit="шт", is_active=1),
            ])
            project = Project(
                telegram_user_id="1", project_name="Тест", client_name="К",
                client_phone="+7",
            )
            session.add(project)
            await session.flush()
            calc = Calculation(
                project_id=project.id,
                modules=[{
                    "type": "lower_base", "width": 600, "depth": 560,
                    "height": 820, "facades": {"count": 1, "type": "doors"},
                }],
                selected_materials={
                    "ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
                    "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000,
                                "price_unit": "м²"},
                    "edge": [{"name": "Кромка 0.8", "thickness_mm": 0.8,
                              "price": 34}],
                },
                selected_hardware={"hinges": {"price": 200, "brand": "FIRMAX"},
                                   "handles": {"price": 500}},
                zone_type="Гостиная",
            )
            session.add(calc)
            await session.commit()

            service = CalculationService(session)
            first = await service.recalculate(calc, reason="initial")

            # Прайс подорожал: ХДФ 1530 → 9999.
            hdf = (await session.execute(
                select(PriceItem).where(PriceItem.name == "ХДФ 3мм")
            )).scalar_one()
            hdf.unit_price = 9999
            await session.commit()

            await scenario(session, service, calc, first)
            await session.close()
        await engine.dispose()

    asyncio.run(wrapper())


def test_saved_calculation_ignores_new_price():
    async def scenario(session, service, calculation, first):
        reopened = await service.preview_calculation(calculation)
        assert abs(reopened.total_cash - first.total_cash) <= 0.01
        assert abs(
            reopened.cost_breakdown.cost_price - first.cost_breakdown.cost_price
        ) <= 0.01
        assert calculation.quote["price_book"]["hdf"] == 1530

    _run(scenario)


def test_current_price_mode_changes_total():
    async def scenario(session, service, calculation, first):
        current = await service.preview_calculation(
            calculation, price_mode="current"
        )
        assert current.cost_breakdown.cost_price > first.cost_breakdown.cost_price
        assert abs(current.total_cash - first.total_cash) > 1.0

    _run(scenario)


def test_discount_after_price_change_uses_snapshot():
    async def scenario(session, service, calculation, first):
        discounted = await service.apply_discount(
            calculation, discount_type="percent", discount_value=10
        )
        # Себестоимость не изменилась от нового прайса.
        assert abs(
            discounted.cost_breakdown.cost_price - first.cost_breakdown.cost_price
        ) <= 0.01
        assert calculation.quote["reason"] == "discount"

    _run(scenario)


def test_policy_change_does_not_move_saved_calculation():
    async def scenario(session, service, calculation, first):
        session.add(CalcSettings(
            name="Новый", is_active=1, is_default=1, manufacturing_rate=0.5
        ))
        await session.commit()

        reopened = await service.preview_calculation(calculation)
        assert abs(
            reopened.cost_breakdown.manufacturing
            - first.cost_breakdown.manufacturing
        ) <= 0.01

        current = await service.preview_calculation(
            calculation, price_mode="current"
        )
        assert current.cost_breakdown.manufacturing < first.cost_breakdown.manufacturing

    _run(scenario)
