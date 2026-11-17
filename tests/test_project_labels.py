"""P2-22: в «Моих проектах» legacy-расчёт помечен «архив», а не ценой."""

from app.bot.handlers.nav import project_row_label
from app.models import Calculation, Project


def _project(*calculations):
    project = Project(
        telegram_user_id="1", project_name="Кухня", client_name="Клиент",
        client_phone="+7",
    )
    project.calculations = list(calculations)
    return project


def test_legacy_calculation_shows_archive_status():
    legacy = Calculation(final_price_cash=85000, cost_price=0, quote=None)
    project = _project(legacy)
    label = project_row_label(project)
    assert "архив" in label
    assert "85" not in label


def test_quote_calculation_shows_price():
    calc = Calculation(
        cost_price=15000, final_price_cash=85000,
        quote={"totals": {"final_cash": 85000}},
    )
    label = project_row_label(_project(calc))
    assert "85 000" in label
    assert "архив" not in label


def test_project_without_calculations():
    label = project_row_label(_project())
    assert "без расчёта" in label


def test_last_calculation_wins():
    old = Calculation(
        cost_price=15000, final_price_cash=10000,
        quote={"totals": {"final_cash": 10000}},
    )
    new = Calculation(
        cost_price=20000, final_price_cash=20000,
        quote={"totals": {"final_cash": 20000}},
    )
    old.id, new.id = 1, 2
    label = project_row_label(_project(old, new))
    assert "20 000" in label
