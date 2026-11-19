"""Тесты админских обработчиков прайса (День 1).

Проверяют совместимость с aiogram 3.31: у `Document` нет `download`,
`InlineKeyboardMarkup` не принимает `row_width` и не имеет `.add`.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bot.handlers import price_admin


class _FakeState:
    def __init__(self):
        self.cleared = False

    async def clear(self):
        self.cleared = True


class _FakeSessionFactory:
    """Минимальная замена AsyncSessionLocal: async with config() as session."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False


def _fake_message(answers, bot_download, document):
    async def answer(text, **kwargs):
        answers.append((text, kwargs))

    return SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        document=document,
        bot=SimpleNamespace(download=bot_download),
        answer=answer,
    )


def test_price_category_keyboard_fits_telegram_callback_limit():
    categories = ["Алюминиевый фасад", "Кромка", "ЛДСП", "Петли", "Ящики"]
    keyboard = price_admin.build_price_category_keyboard(categories)

    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert [button.text for button in buttons] == categories
    assert [button.callback_data for button in buttons] == [
        f"price_category:{index}" for index in range(len(categories))
    ]
    # Telegram: callback_data ≤ 64 байт. Название кириллицей в percent-кодировке
    # («Алюминиевый фасад» → 123 байта) ломало кнопку.
    assert all(len(button.callback_data.encode("utf-8")) <= 64 for button in buttons)
    assert [len(row) for row in keyboard.inline_keyboard] == [2, 2, 1]


def test_receive_price_document_downloads_via_bot(monkeypatch, tmp_path):
    """aiogram 3.31: у Document нет download, файл скачивает message.bot."""
    answers: list = []
    downloaded = {}

    async def bot_download(document, destination):
        downloaded["document"] = document
        downloaded["destination"] = Path(destination)
        Path(destination).write_bytes(b"xlsx-bytes")

    async def fake_import(path, session, changed_by=None):
        # Файл уже скачан ботом и лежит по ожидаемому пути.
        assert Path(path).read_bytes() == b"xlsx-bytes"
        assert Path(path).name == "price.xlsx"
        return {
            "created": 1,
            "updated": 2,
            "skipped": 3,
            "rejected": 4,
            "deactivated": 5,
        }

    document = SimpleNamespace(
        file_name="../../price.xlsx",
        file_size=1024,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    message = _fake_message(answers, bot_download, document)
    state = _FakeState()

    monkeypatch.setattr(price_admin, "_admin_denied", lambda _: False)
    monkeypatch.setattr(price_admin, "AsyncSessionLocal", _FakeSessionFactory(object()))
    monkeypatch.setattr(price_admin, "import_price_from_xlsx", fake_import)

    asyncio.run(price_admin.receive_price_document(message, state))

    # Path(tmpdir) / "../../price.xlsx" сводится к basename — без выхода наружу.
    assert downloaded["destination"].name == "price.xlsx"
    assert ".." not in downloaded["destination"].parts
    assert state.cleared is True
    assert "✅" in answers[-1][0]
    assert "Создано: 1" in answers[-1][0]
    assert "обновлено: 2" in answers[-1][0]
    assert "деактивировано: 5" in answers[-1][0]


def test_receive_price_document_reports_download_error(monkeypatch):
    answers: list = []
    state = _FakeState()

    async def failing_download(document, destination):
        raise OSError("network down")

    document = SimpleNamespace(
        file_name="price.xlsx",
        file_size=1024,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    message = _fake_message(answers, failing_download, document)

    monkeypatch.setattr(price_admin, "_admin_denied", lambda _: False)

    asyncio.run(price_admin.receive_price_document(message, state))

    assert state.cleared is True
    assert "❌" in answers[-1][0]


def test_callback_price_category_resolves_index(monkeypatch):
    """Новый callback — индекс; старый — percent-кодированное название."""
    answers: list = []
    callbacks: list = []

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

        def scalars(self):
            return self

        def all(self):
            return [
                SimpleNamespace(
                    id=1,
                    name="Петля BOYARD",
                    unit_price=145.0,
                    price_unit="шт",
                )
            ]

    class _Session:
        async def execute(self, stmt):
            text = str(stmt)
            if "price_items.category" in text and "ORDER BY" in text.upper():
                return _Result([("Кромка",), ("Петли",), ("Ящики",)])
            return _Result([])

    async def answer(text, **kwargs):
        answers.append(text)

    async def cb_answer(text=None, **kwargs):
        callbacks.append(text)

    callback = SimpleNamespace(
        data="price_category:1",
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(answer=answer),
        answer=cb_answer,
    )

    monkeypatch.setattr(price_admin, "_admin_denied", lambda _: False)
    monkeypatch.setattr(price_admin, "AsyncSessionLocal", _FakeSessionFactory(_Session()))

    asyncio.run(price_admin.callback_price_category(callback))

    assert "Категория: Петли" in answers[-1]
    assert "Петля BOYARD" in answers[-1]


@pytest.mark.parametrize("payload", ["Кромка", "%D0%9F%D0%B5%D1%82%D0%BB%D0%B8"])
def test_callback_price_category_supports_legacy_payload(monkeypatch, payload):
    answers: list = []

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

        def scalars(self):
            return self

        def all(self):
            return []

    class _Session:
        async def execute(self, stmt):
            return _Result([])

    async def answer(text, **kwargs):
        answers.append(text)

    async def cb_answer(text=None, **kwargs):
        pass

    callback = SimpleNamespace(
        data=f"price_category:{payload}",
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(answer=answer),
        answer=cb_answer,
    )

    monkeypatch.setattr(price_admin, "_admin_denied", lambda _: False)
    monkeypatch.setattr(price_admin, "AsyncSessionLocal", _FakeSessionFactory(_Session()))

    asyncio.run(price_admin.callback_price_category(callback))

    assert "В этой категории нет активных позиций" in answers[-1]


def _message_with_text(answers, text):
    async def answer(reply, **kwargs):
        answers.append(reply)

    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=42),
        answer=answer,
    )


def test_price_search_requires_query(monkeypatch):
    answers: list = []
    monkeypatch.setattr(price_admin, "_admin_denied", lambda _: False)

    asyncio.run(price_admin.cmd_price_search(_message_with_text(answers, "/price_search")))

    assert "Использование: /price_search" in answers[-1]


def test_price_search_lists_matches(monkeypatch):
    answers: list = []

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [
                SimpleNamespace(
                    id=10,
                    name="EGGER 2*19",
                    category="Кромка",
                    brand="EGGER",
                    unit_price=60.0,
                    price_unit="м",
                    requires_manual_price=0,
                )
            ]

    class _Session:
        async def execute(self, stmt):
            assert "price_items.name" in str(stmt)
            return _Result()

    monkeypatch.setattr(price_admin, "_admin_denied", lambda _: False)
    monkeypatch.setattr(price_admin, "AsyncSessionLocal", _FakeSessionFactory(_Session()))

    asyncio.run(price_admin.cmd_price_search(_message_with_text(answers, "/price_search EGGER")))

    assert "EGGER 2*19" in answers[-1]
    assert "60.00" in answers[-1]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/price_edit", "Использование: /price_edit"),
        ("/price_edit abc 100", "id должен быть целым числом"),
        ("/price_edit 5 -10", "корректную неотрицательную цену"),
        ("/price_edit 5 дорого", "корректную неотрицательную цену"),
    ],
)
def test_price_edit_validates_input(monkeypatch, text, expected):
    answers: list = []
    monkeypatch.setattr(price_admin, "_admin_denied", lambda _: False)

    asyncio.run(price_admin.cmd_price_edit(_message_with_text(answers, text)))

    assert expected in answers[-1]
