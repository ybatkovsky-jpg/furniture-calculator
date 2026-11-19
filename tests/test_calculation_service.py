"""P1-01/P0-01: единый сервис пересчёта и полный пересчёт скидки.

Каждый тест доказывает то, что раньше было сломано:
- скидка не «теряет» ХДФ/столешницу/крепёж (раньше 5 компонентов из 10);
- все экраны (смета, скидка, повторное открытие) дают одно число;
- невалидная скидка не меняет БД;
- снимок `quote` обновляется вместе с полями расчёта.
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.validation import PricingError
from app.models import Base, Calculation, PriceItem, Project
from app.services.calculation_service import CalculationService, discount_info_from
from app.services.cost_calc import DiscountInfo, calculate_cost
from app.services.pricing_policy import PricingPolicy


def _module():
    return {
        "type": "lower_base",
        "width": 600,
        "depth": 560,
        "height": 820,
        "quantity": 1,
        "facades": {"count": 2, "type": "doors"},
        "drawers": {"count": 2},
        "shelves": 1,
    }


def _materials():
    return {
        "ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист"},
        "facades": {"name": "ФАСАДЫ ПВХ 16мм", "price": 10000, "price_unit": "м²"},
        "edge": [
            {"name": "Кромка 0.8", "thickness_mm": 0.8, "price": 34},
            {"name": "Кромка 0.4", "thickness_mm": 0.4, "price": 17.5},
        ],
    }


def _hardware():
    return {
        "hinges": {"price": 200, "brand": "FIRMAX"},
        "drawers": {"price": 3000, "name": "Tandembox"},
        "handles": {"price": 500},
    }


def _price_items():
    return [
        PriceItem(name="ХДФ 3мм", category="ХДФ", unit_price=1530,
                  price_unit="лист", is_active=1),
        PriceItem(name="Конфирмат 7×50", category="Крепёж", unit_price=2,
                  price_unit="шт", is_active=1),
        PriceItem(name="Регулируемая опора", category="Крепёж", unit_price=50,
                  price_unit="шт", is_active=1),
        PriceItem(name="Настенный подвес", category="Крепёж", unit_price=120,
                  price_unit="шт", is_active=1),
        PriceItem(name="Цоколь ПВХ 4м", category="Крепёж", unit_price=1500,
                  price_unit="шт", is_active=1),
    ]


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    session.add_all(_price_items())
    project = Project(
        telegram_user_id="1", project_name="Тест", client_name="Клиент",
        client_phone="+70000000000",
    )
    session.add(project)
    await session.flush()
    calculation = Calculation(
        project_id=project.id,
        variant_name="Вариант 1",
        modules=[_module()],
        selected_materials=_materials(),
        selected_hardware=_hardware(),
        glass_items=[],
        zone_type="Гостиная",
    )
    session.add(calculation)
    await session.commit()
    return engine, session, calculation


def _run(scenario):
    async def wrapper():
        engine, session, calculation = await _make_session()
        try:
            await scenario(session, calculation)
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(wrapper())


def test_recalculate_is_full_and_persisted():
    async def scenario(session, calculation):
        service = CalculationService(session)
        result = await service.recalculate(calculation, reason="initial")

        # Все 10 компонентов на месте: скидка больше не увидит «дыру».
        # 23 578 = 23 473 + 105 ₽: P2-12 — скрытая кромка 0,4 мм больше не
        # уменьшается от добавления дверей (16 м вместо 10).
        assert result.cost_breakdown.cost_price == 23578
        assert calculation.cost_price == result.cost_breakdown.cost_price
        assert calculation.final_price_cash == result.total_cash
        assert calculation.final_price_noncash == result.total_noncash
        assert calculation.quote is not None
        assert calculation.quote["reason"] == "initial"
        assert calculation.quote["can_issue_kp"] is True
        # Сумма строк = себестоимость.
        assert abs(sum(line.amount for line in result.lines) - calculation.cost_price) <= 0.01

    _run(scenario)


def test_discount_keeps_all_components_and_matches_engine():
    """P0-01: скидка 10 % считается от полной базы, а не от 5 компонентов."""

    async def scenario(session, calculation):
        service = CalculationService(session)
        base = await service.recalculate(calculation, reason="initial")

        result = await service.apply_discount(
            calculation, discount_type="percent", discount_value=10
        )

        # Себестоимость и производные не меняются от скидки.
        assert result.cost_breakdown.cost_price == base.cost_breakdown.cost_price
        assert result.cost_breakdown.manufacturing == base.cost_breakdown.manufacturing
        assert result.cost_breakdown.profit == base.cost_breakdown.profit
        assert result.cost_breakdown.subtotal == base.cost_breakdown.subtotal

        # Итог = база − скидка; скидка = 10 % полной базы (округление до ₽).
        base_cash = result.cost_breakdown.total_base_cash
        discount_amount = result.cost_breakdown.discount_amount
        furniture = result.total_cash - result.cost_breakdown.delivery_client
        assert abs(discount_amount - base_cash * 0.10) <= 0.5
        assert abs(furniture - (base_cash - discount_amount)) <= 0.01

        # Поля БД и снимок согласованы.
        assert calculation.final_price_cash == result.total_cash
        assert calculation.discount_type == "percent"
        assert calculation.discount_value == 10
        assert calculation.quote["discount_amount"] == result.cost_breakdown.discount_amount
        assert calculation.quote["reason"] == "discount"

        # Регресс-доказательство: старый путь «5 компонентов из 10» даёт
        # другое (заниженное) число — значит тест реально ловит P0-01.
        buggy = calculate_cost(
            material_cost=result.material_cost,
            edge_cost=result.edge_cost,
            facade_cost=result.facade_cost,
            hardware_cost=result.hardware_cost,
            glass_cost=result.glass_cost,
            discount=DiscountInfo(discount_type="percent", discount_value=10),
            **PricingPolicy.from_config().as_cost_kwargs(),
        )
        assert abs(buggy.final_cash - result.total_cash) > 1.0

    _run(scenario)


def test_invalid_discount_does_not_change_db():
    async def scenario(session, calculation):
        service = CalculationService(session)
        base = await service.recalculate(calculation, reason="initial")
        before = (calculation.final_price_cash, calculation.discount_type)

        with pytest.raises(PricingError):
            await service.apply_discount(
                calculation, discount_type="fixed", discount_value=10_000_000
            )

        assert (calculation.final_price_cash, calculation.discount_type) == before
        assert calculation.final_price_cash == base.total_cash

    _run(scenario)


def test_discount_survives_reopen():
    """P1-11: повторное открытие показывает цену со скидкой, не базовую."""

    async def scenario(session, calculation):
        service = CalculationService(session)
        base = await service.recalculate(calculation, reason="initial")
        discounted = await service.apply_discount(
            calculation, discount_type="percent", discount_value=10
        )
        assert discounted.total_cash < base.total_cash

        # Экран «Мои проекты» — preview по сохранённым полям, без записи.
        reopened = await service.preview_calculation(calculation)
        assert abs(reopened.total_cash - discounted.total_cash) <= 0.01
        stored = await service.preview_calculation(
            calculation, discount=discount_info_from(calculation)
        )
        assert abs(stored.total_cash - discounted.total_cash) <= 0.01

    _run(scenario)


def test_clear_discount_returns_to_base():
    async def scenario(session, calculation):
        service = CalculationService(session)
        base = await service.recalculate(calculation, reason="initial")
        await service.apply_discount(
            calculation, discount_type="percent", discount_value=10
        )
        cleared = await service.clear_discount(calculation)
        assert abs(cleared.total_cash - base.total_cash) <= 0.01
        assert calculation.discount_type is None
        assert calculation.discount_value == 0

    _run(scenario)


def test_discount_above_ten_percent_requires_approval():
    """P1-16: скидка 15 % без согласования директора не применяется."""

    async def scenario(session, calculation):
        service = CalculationService(session)
        await service.recalculate(calculation, reason="initial")
        before = (calculation.final_price_cash, calculation.discount_type)

        with pytest.raises(PricingError, match="согласование директора"):
            await service.apply_discount(
                calculation, discount_type="percent", discount_value=15
            )

        assert (calculation.final_price_cash, calculation.discount_type) == before

        # Согласование — явный флаг: тогда та же скидка проходит.
        approved = await service.apply_discount(
            calculation,
            discount_type="percent",
            discount_value=15,
            allow_above_limit=True,
        )
        assert approved is not None
        assert calculation.discount_value == 15

    _run(scenario)


def test_ten_percent_discount_passes_margin_gate():
    """10 % при пороге маржи 15 % не уводит цену ниже порога."""

    async def scenario(session, calculation):
        service = CalculationService(session)
        result = await service.recalculate(calculation, reason="initial")
        discounted = await service.apply_discount(
            calculation, discount_type="percent", discount_value=10
        )
        assert discounted.cost_breakdown.below_cost is False
        assert discounted.can_issue_kp is True
        assert discounted.total_cash < result.total_cash

    _run(scenario)


def test_designer_bonus_raises_price_and_is_persisted():
    async def scenario(session, calculation):
        service = CalculationService(session)
        base = await service.recalculate(calculation, reason="initial")
        result = await service.set_designer_bonus(calculation, True)
        assert result.total_cash > base.total_cash
        assert result.cost_breakdown.designer_bonus_amount > 0
        assert calculation.designer_bonus_enabled == 1
        assert calculation.final_price_cash == result.total_cash

    _run(scenario)
