"""P2-05: «Мои проекты» не подменяет текущий проект первым из списка.

Раньше `show_projects` безусловно писал `project_id=projects[0].id`. После
просмотра списка следующее фото/PDF уходило в самый свежий проект, а не в
выбранный. Теперь project_id ставится только явным выбором проекта и
проверяется по владельцу.
"""

import asyncio
from types import SimpleNamespace

from app.bot.handlers import nav
from app.models import Calculation, Project


class FakeState:
    def __init__(self):
        self.data = {}

    async def update_data(self, **kwargs):
        self.data.update(kwargs)


class FakeMessage:
    def __init__(self, user_id=1):
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)


class FakeCallback:
    def __init__(self, data, user_id=1):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = FakeMessage(user_id)
        self.alerts = []

    async def answer(self, text=None, **kwargs):
        self.alerts.append(text)


class FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class FakeSession:
    def __init__(self, projects):
        self._projects = projects

    async def execute(self, _query):
        return FakeResult(self._projects)


def _project(project_id: int, with_calculation: bool = False) -> Project:
    project = Project(
        telegram_user_id="1", project_name=f"Проект {project_id}",
        client_name="Клиент", client_phone="+7",
    )
    project.id = project_id
    project.calculations = []
    if with_calculation:
        calculation = Calculation(
            cost_price=15000, final_price_cash=85000,
            quote={"totals": {"final_cash": 85000}},
        )
        calculation.id = 100 + project_id
        project.calculations = [calculation]
    return project


def _patch_session(monkeypatch, projects):
    async def fake_session():
        yield FakeSession(projects)

    monkeypatch.setattr(nav, "get_session", fake_session)


def test_show_projects_does_not_pick_first_project(monkeypatch):
    _patch_session(monkeypatch, [_project(1), _project(2)])
    state = FakeState()

    asyncio.run(nav.show_projects(FakeMessage(), state))

    assert "project_id" not in state.data


def test_foreign_project_is_rejected(monkeypatch):
    _patch_session(monkeypatch, [])

    async def not_owned(_session, _project_id, _user_id):
        return None

    monkeypatch.setattr(nav, "get_owned_project", not_owned)
    callback = FakeCallback("project_no_calc:99")
    state = FakeState()

    asyncio.run(nav.project_without_calculation(callback, state))

    assert callback.alerts[-1] == "Проект не найден"
    assert "project_id" not in state.data


def test_open_estimate_sets_its_project(monkeypatch):
    _patch_session(monkeypatch, [])
    calculation = SimpleNamespace(id=5, project_id=42)

    async def owned(_session, _calculation_id, _user_id):
        return calculation

    class FakeService:
        def __init__(self, _session):
            pass

        async def preview_calculation(self, _calculation):
            return "preview"

    shown = []

    async def fake_show_estimate(message, calc, result):
        shown.append((calc, result))

    monkeypatch.setattr(nav, "get_owned_calculation", owned)
    monkeypatch.setattr(nav, "CalculationService", FakeService)
    monkeypatch.setattr(nav, "show_estimate", fake_show_estimate)

    callback = FakeCallback("show_estimate:5")
    state = FakeState()
    asyncio.run(nav.show_estimate_callback(callback, state))

    assert state.data["project_id"] == 42
    assert shown and shown[0][1] == "preview"
