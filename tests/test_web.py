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
