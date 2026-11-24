"""Веб-интерфейс: страница и валидация загрузки (без сети и без модели)."""

from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.web.server import app

client = TestClient(app)


def test_index_returns_page() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "ПРО МЕБЕЛЬ" in response.text
    assert "Скачать Excel" in response.text


def test_upload_rejects_unsupported_extension() -> None:
    response = client.post(
        "/api/analyze",
        files={"file": ("смета.txt", io.BytesIO(b"not an image"), "text/plain")},
    )
    assert response.status_code == 400
    assert "Поддерживаются изображения" in response.json()["detail"]


def test_excel_unknown_quote_returns_404() -> None:
    response = client.get("/api/excel/нет-такого")
    assert response.status_code == 404


def test_kp_unknown_quote_returns_404() -> None:
    response = client.post("/api/kp/нет-такого", json={"variants": ["Базовая"]})
    assert response.status_code == 404


def test_kp_endpoint_returns_docx(monkeypatch) -> None:
    """КП собирается из сохранённого состава; модель и БД не нужны."""
    from app.web import server
    from tests.test_kp_variants import MODULES, build_variants, catalog

    server.QUOTES["test-quote"] = {
        "quote": {},
        "modules": MODULES,
        "zone": "Кухня",
        "image": None,
    }
    monkeypatch.setattr(
        server,
        "build_variants",
        lambda modules, **kwargs: build_variants(modules, catalog=catalog(), **kwargs),
    )
    response = client.post(
        "/api/kp/test-quote",
        json={"variants": ["Базовая", "Стандарт"], "client": "Тест", "image": False},
    )
    assert response.status_code == 200
    assert response.content[:2] == b"PK"
    assert "wordprocessingml" in response.headers["content-type"]


MATERIALS = {
    "ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист", "thickness_mm": 16, "brand": "EGGER"},
    "edge": {"name": "EGGER 0,4", "price": 22},
    "facades": {"name": "АКРИЛ", "price": 6300, "price_unit": "м²"},
}
HARDWARE = {"hinges": {"name": "Петля", "price": 300}, "handles": {"name": "Ручка", "price": 550}}


def _prepare_quote(monkeypatch, modules=None) -> str:
    from app.web import server

    monkeypatch.setattr(server, "default_materials", lambda: (MATERIALS, HARDWARE))
    monkeypatch.setattr(server, "load_price_book_sync", lambda: {})
    server.QUOTES["edit-quote"] = {
        "quote": {},
        "modules": modules or [],
        "zone": "Кухня",
        "image": None,
    }
    return "edit-quote"


def test_recalc_returns_totals(monkeypatch) -> None:
    quote = _prepare_quote(monkeypatch)
    modules = [
        {"type": "lower_base", "width": 600, "depth": 560, "height": 820,
         "quantity": 1, "facades": {"count": 2}, "drawers": {"count": 1}},
    ]
    response = client.post(f"/api/recalc/{quote}", json={"modules": modules})
    assert response.status_code == 200
    data = response.json()
    assert data["doors"] == 2
    assert data["drawers"] == 1
    assert data["totals"]["final_cash"] > 0
    assert len(data["modules"]) == 1


def test_recalc_rejects_zero_width(monkeypatch) -> None:
    quote = _prepare_quote(monkeypatch)
    response = client.post(
        f"/api/recalc/{quote}",
        json={"modules": [{"type": "lower_base", "width": 0, "depth": 560, "height": 820}]},
    )
    assert response.status_code == 400
    assert "ширина и высота" in response.json()["detail"]


def test_recalc_rejects_unknown_type(monkeypatch) -> None:
    quote = _prepare_quote(monkeypatch)
    response = client.post(
        f"/api/recalc/{quote}",
        json={"modules": [{"type": "диван", "width": 900, "depth": 600, "height": 800}]},
    )
    assert response.status_code == 400
    assert "неизвестный тип" in response.json()["detail"]


def test_recalc_unknown_quote_returns_404() -> None:
    response = client.post("/api/recalc/нет-такого", json={"modules": []})
    assert response.status_code == 404


def test_recalc_updates_saved_snapshot(monkeypatch) -> None:
    """После правки Excel и КП должны взять новый состав."""
    from app.web import server

    quote = _prepare_quote(monkeypatch)
    modules = [
        {"type": "upper_base", "width": 800, "depth": 320, "height": 720,
         "quantity": 2, "facades": {"count": 2}, "drawers": {"count": 0}},
    ]
    response = client.post(f"/api/recalc/{quote}", json={"modules": modules})
    assert response.status_code == 200
    assert len(server.QUOTES[quote]["modules"]) == 1
    assert server.QUOTES[quote]["quote"]  # снимок пересобран
