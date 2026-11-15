"""Тесты этапа 0: fail-closed админ (P0-05) и доступ к расчётам (P1-14)."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base, Calculation, Project
from app.services.access import get_owned_calculation, get_owned_project


# ── P0-05: fail-closed административный доступ ──────────────────────────────


def test_admin_empty_list_denies_everyone():
    """Пустой ADMIN_TELEGRAM_IDS = админов нет (раньше было «все админы»)."""
    s = Settings(admin_telegram_ids="")
    assert s.is_admin(1) is False
    assert s.is_admin("123456789") is False


def test_admin_list_allows_only_listed_ids():
    s = Settings(admin_telegram_ids=" 1, 2 ,3 ")
    assert s.is_admin(1) is True
    assert s.is_admin("2") is True
    assert s.is_admin(3) is True
    assert s.is_admin(4) is False
    assert s.is_admin("") is False


def test_allowlist_empty_means_no_restriction():
    s = Settings(allowed_telegram_ids="")
    assert s.is_allowed(999) is True


def test_allowlist_restricts_and_trusts_admins():
    s = Settings(admin_telegram_ids="1", allowed_telegram_ids="2,3")
    assert s.is_allowed(2) is True
    assert s.is_allowed("3") is True
    assert s.is_allowed(1) is True  # админ всегда допущен
    assert s.is_allowed(4) is False


def test_production_requires_admins():
    """В prod пустой список админов — ошибка конфигурации, а не тихий старт."""
    prod = Settings(environment="prod", admin_telegram_ids="")
    with pytest.raises(RuntimeError, match="ADMIN_TELEGRAM_IDS"):
        prod.validate_startup()

    ok = Settings(environment="production", admin_telegram_ids="1")
    ok.validate_startup()  # не падает

    dev = Settings(environment="dev", admin_telegram_ids="")
    dev.validate_startup()  # в dev — только предупреждение в логе


# ── P1-14: владелец расчёта ─────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def test_owned_calculation_scoped_to_owner():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            mine = Project(
                telegram_user_id="100", client_name="Мой", client_phone="1"
            )
            alien = Project(
                telegram_user_id="200", client_name="Чужой", client_phone="2"
            )
            session.add_all([mine, alien])
            await session.flush()
            my_calc = Calculation(project_id=mine.id)
            alien_calc = Calculation(project_id=alien.id)
            session.add_all([my_calc, alien_calc])
            await session.commit()

            own = await get_owned_calculation(session, my_calc.id, 100)
            assert own is not None and own.id == my_calc.id

            # Чужой id неотличим от несуществующего.
            assert await get_owned_calculation(session, alien_calc.id, 100) is None
            assert await get_owned_calculation(session, my_calc.id, 200) is None
            assert await get_owned_calculation(session, 999999, 100) is None

            assert await get_owned_project(session, alien.id, 100) is None
            assert (await get_owned_project(session, mine.id, 100)).id == mine.id
        await engine.dispose()

    _run(scenario())
