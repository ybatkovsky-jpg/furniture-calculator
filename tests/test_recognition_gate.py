"""P1-13: низкая уверенность распознавания блокирует КП.

Уверенность и quality_score сохраняются в расчёте и попадают в снимок; КП
не выпускается, пока оператор не проверит состав. Ошибка модели больше не
превращается в смету для клиента молча.
"""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Calculation, PriceItem, Project
from app.services.calculation_service import CalculationService

LOW_WARNING = "Низкая уверенность распознавания"


def _run(confidence=None, quality=None):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add_all([
                PriceItem(name="ХДФ 3мм", category="ХДФ", unit_price=1530,
                          price_unit="лист", is_active=1),
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
                recognition_confidence=confidence,
                quality_score=quality,
            )
            session.add(calc)
            await session.commit()
            result = await CalculationService(session).recalculate(
                calc, reason="initial"
            )
            quote = dict(calc.quote or {})
            await session.close()
        await engine.dispose()
        return result, quote

    return asyncio.run(scenario())


def test_low_confidence_blocks_kp_and_is_in_quote():
    result, quote = _run(confidence="low")
    assert result.can_issue_kp is False
    assert any(LOW_WARNING in warning for warning in result.warnings)
    assert quote["can_issue_kp"] is False
    assert quote["recognition"]["confidence"] == "low"
    assert any(LOW_WARNING in reason for reason in quote["blocking_reasons"])


def test_low_quality_score_blocks_kp():
    result, quote = _run(confidence="high", quality=0.5)
    assert result.can_issue_kp is False
    assert any("Низкое качество распознавания" in w for w in result.warnings)
    assert quote["recognition"]["quality_score"] == 0.5


def test_high_confidence_does_not_block():
    result, quote = _run(confidence="high", quality=0.9)
    assert result.can_issue_kp is True
    assert quote["blocking_reasons"] == []
    assert quote["recognition"]["confidence"] == "high"


def test_manual_input_without_confidence_is_not_blocked():
    result, quote = _run(confidence=None, quality=None)
    assert result.can_issue_kp is True
    assert quote["recognition"]["confidence"] is None
