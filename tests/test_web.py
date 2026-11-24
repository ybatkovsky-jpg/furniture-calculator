"""Веб-интерфейс: страница и валидация загрузки (без сети и без модели)."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.web.server import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_db_status(monkeypatch):
    """Веб-тесты не пишут статусы и версии в рабочую БД."""
    from app.web import server

    async def no_status(project_id, status):
        return False

    async def no_version(quote_id, data, **kwargs):
        return None

    monkeypatch.setattr(server, "set_project_status", no_status)
    monkeypatch.setattr(server, "save_web_quote_version", no_version)


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


def test_get_quote_restores_from_db(monkeypatch) -> None:
    """После «перезапуска» кэш пуст, но снимок читается из web_quotes."""
    import asyncio

    from app.web import server

    server.QUOTES.pop("restored-quote", None)

    async def fake_load(quote_id):
        return {
            "quote": {"total_cash": 123},
            "modules": [],
            "zone": "Кухня",
            "image": None,
            "project_id": "a" * 16,
            "page_index": 0,
        }

    monkeypatch.setattr(server, "load_web_quote", fake_load)
    data = asyncio.run(server._get_quote("restored-quote"))

    assert data is not None
    assert data["quote"]["total_cash"] == 123
    assert server.QUOTES["restored-quote"] is data


def test_project_page_metadata_can_be_set(tmp_path, monkeypatch) -> None:
    from app.services.importing import ProjectImportService
    from app.web import server

    monkeypatch.setattr(server, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(server, "INCOMING_DIR", tmp_path / "incoming")

    image = io.BytesIO()
    from PIL import Image

    Image.new("RGB", (40, 30), "white").save(image, format="PNG")
    imported = client.post(
        "/api/projects/import",
        files={"file": ("план.png", image.getvalue(), "image/png")},
    ).json()

    response = client.patch(
        f"/api/projects/{imported['project_id']}/pages/0",
        json={
            "room_name": "Кухня",
            "view_type": "plan",
            "view_group": "kitchen_main",
        },
    )
    assert response.status_code == 200
    assert response.json()["page"]["room_id"] == "Кухня"

    project = ProjectImportService(tmp_path / "projects").store.load(
        imported["project_id"]
    )
    assert project is not None
    assert project.pages[0].view_type == "plan"
    assert project.pages[0].view_group == "kitchen_main"


def test_quote_versions_endpoint(monkeypatch) -> None:
    from app.web import server

    async def fake_versions(quote_id):
        return [{"version_no": 1, "reason": "initial"}]

    monkeypatch.setattr(server, "list_web_quote_versions", fake_versions)
    response = client.get("/api/quotes/abc/versions")
    assert response.status_code == 200
    assert response.json()["versions"][0]["version_no"] == 1


def _pdf_bytes(pages: int = 2) -> bytes:
    import fitz

    document = fitz.open()
    for index in range(pages):
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 72), f"page {index + 1}")
    data = document.tobytes()
    document.close()
    return data


def test_project_import_accepts_pdf_and_serves_pages(tmp_path, monkeypatch) -> None:
    from app.web import server

    monkeypatch.setattr(server, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(server, "INCOMING_DIR", tmp_path / "incoming")

    response = client.post(
        "/api/projects/import",
        files={"file": ("альбом.pdf", _pdf_bytes(2), "application/pdf")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["source_type"] == "pdf"
    assert len(data["pages"]) == 2
    assert data["ready_for_recognition"] is True

    page = client.get(data["pages"][0]["url"])
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("image/")


def test_project_import_sto_is_not_ready_without_export(tmp_path, monkeypatch) -> None:
    from app.web import server

    monkeypatch.setattr(server, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(server, "INCOMING_DIR", tmp_path / "incoming")

    response = client.post(
        "/api/projects/import",
        files={"file": ("Проект.sto", b"pro100-binary", "application/octet-stream")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ready_for_recognition"] is False
    assert data["metadata"]["parsed"] is False
    assert any(".sto" in warning for warning in data["warnings"])


def test_project_import_details_txt_is_ready(tmp_path, monkeypatch) -> None:
    from app.web import server

    monkeypatch.setattr(server, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(server, "INCOMING_DIR", tmp_path / "incoming")

    response = client.post(
        "/api/projects/import",
        files={
            "file": (
                "details.txt",
                "Полка\t400\t500\t16\t1\tЦвета\\Белый\n".encode(),
                "text/plain",
            )
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metadata"]["details_count"] == 1
    assert data["ready_for_recognition"] is True


def test_project_page_analyze_returns_quote(tmp_path, monkeypatch) -> None:
    from app.services.image_analyzer import RecognizedModule
    from app.web import server

    monkeypatch.setattr(server, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(server, "INCOMING_DIR", tmp_path / "incoming")
    monkeypatch.setattr(server, "default_materials", lambda: (MATERIALS, HARDWARE))
    monkeypatch.setattr(server, "load_price_book_sync", dict)

    async def no_save(quote_id, data):
        return False

    monkeypatch.setattr(server, "save_web_quote", no_save)

    class FakeAnalyzer:
        async def analyze_page(self, path, return_meta=True):
            module = RecognizedModule(
                type="lower_base",
                width=600,
                depth=560,
                height=820,
                quantity=1,
                facades={"count": 2},
                drawers={"count": 1},
            )
            return [module], "Кухня", [], "high", {
                "total_width_mm": 600,
                "door_count": 2,
                "drawer_count": 1,
                "quality_flags": [],
            }

        async def close(self):
            return None

    monkeypatch.setattr(server, "GeminiImageAnalyzer", FakeAnalyzer)

    image = io.BytesIO()
    from PIL import Image

    Image.new("RGB", (80, 60), "white").save(image, format="PNG")
    imported = client.post(
        "/api/projects/import",
        files={"file": ("план.png", image.getvalue(), "image/png")},
    ).json()

    response = client.post(
        f"/api/projects/{imported['project_id']}/analyze",
        json={"page_index": 0},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["doors"] == 2
    assert data["drawers"] == 1
    assert data["validation"]["can_calculate"] is True
    assert data["validation"]["values"][0]["source_type"] == "vision"
    assert data["project_status"] == "CALCULATED"
    assert data["quote_id"] in server.QUOTES
    assert data["project_id"] == imported["project_id"]



MATERIALS = {
    "ldsp": {"name": "EGGER", "price": 6000, "price_unit": "лист", "thickness_mm": 16, "brand": "EGGER"},
    "edge": {"name": "EGGER 0,4", "price": 22},
    "facades": {"name": "АКРИЛ", "price": 6300, "price_unit": "м²"},
}
HARDWARE = {"hinges": {"name": "Петля", "price": 300}, "handles": {"name": "Ручка", "price": 550}}


def _prepare_quote(monkeypatch, modules=None) -> str:
    from app.web import server

    async def no_save(quote_id, data):
        return False

    monkeypatch.setattr(server, "default_materials", lambda: (MATERIALS, HARDWARE))
    monkeypatch.setattr(server, "load_price_book_sync", dict)
    monkeypatch.setattr(server, "save_web_quote", no_save)
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
    assert data["validation"]["values"][0]["source_type"] == "manual"


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
