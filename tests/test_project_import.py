"""Единый импорт проекта: изображения, PDF, выгрузка ПРО100 (этап 1)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import fitz
import pytest
from PIL import Image

from app.services.importing import (
    ImportValidationError,
    ProjectImportService,
    UnsupportedSourceError,
    parse_details_line,
    safe_filename,
)
from app.services.importing.base import content_id


def run(coro):
    return asyncio.run(coro)


def _png(path: Path, size: tuple[int, int] = (120, 80)) -> Path:
    Image.new("RGB", size, "white").save(path)
    return path


def _pdf(path: Path, pages: int = 2) -> Path:
    document = fitz.open()
    for index in range(pages):
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 72), f"page {index + 1}")
    document.save(path)
    document.close()
    return path


def test_image_project_is_saved_with_stable_id(tmp_path: Path) -> None:
    source = _png(tmp_path / "план.png")
    service = ProjectImportService(tmp_path / "projects")

    project = run(service.import_project(source))

    assert project.source_type == "image"
    assert project.original_filename == "план.png"
    assert len(project.pages) == 1
    assert Path(project.pages[0].image_path).is_file()
    assert project.pages[0].width_px == 120
    assert project.pages[0].height_px == 80
    assert (tmp_path / "projects" / project.project_id / "project.json").is_file()


def test_reimport_returns_same_project_without_copy(tmp_path: Path) -> None:
    source = _png(tmp_path / "план.png")
    service = ProjectImportService(tmp_path / "projects")

    first = run(service.import_project(source))
    second = run(service.import_project(source))

    assert second.project_id == first.project_id
    assert second.is_duplicate is True
    stored = list((tmp_path / "projects" / first.project_id / "source").iterdir())
    assert len(stored) == 1


def test_duplicate_survives_new_service_instance(tmp_path: Path) -> None:
    source = _png(tmp_path / "план.png")
    first = run(ProjectImportService(tmp_path / "projects").import_project(source))

    second = run(ProjectImportService(tmp_path / "projects").import_project(source))

    assert first.project_id == second.project_id
    assert second.is_duplicate is True


def test_pdf_is_split_into_pages(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "альбом.pdf", pages=2)
    service = ProjectImportService(tmp_path / "projects")
    project = run(service.import_project(source))

    assert project.source_type == "pdf"
    assert project.metadata["page_count"] == 2
    assert len(project.pages) == 2
    # Рендер ленивый: на импорте есть только метаданные страниц.
    assert all(page.image_path is None for page in project.pages)
    assert 200 <= project.pages[0].width_mm <= 220

    page = service.ensure_page_image(project.project_id, 0)
    assert page.image_path and Path(page.image_path).is_file()
    assert page.width_px and page.height_px
    # Повторный запрос не рендерит заново и не теряет путь.
    assert service.ensure_page_image(project.project_id, 0).image_path == page.image_path


def test_corrupt_pdf_has_readable_error(tmp_path: Path) -> None:
    source = tmp_path / "битый.pdf"
    source.write_bytes(b"not a pdf at all")

    with pytest.raises(ImportValidationError) as error:
        run(ProjectImportService(tmp_path / "projects").import_project(source))

    assert "PDF" in str(error.value)


def test_corrupt_image_has_readable_error(tmp_path: Path) -> None:
    source = tmp_path / "битый.png"
    source.write_bytes(b"not an image")

    with pytest.raises(ImportValidationError) as error:
        run(ProjectImportService(tmp_path / "projects").import_project(source))

    assert "изображение" in str(error.value).lower()


def test_encrypted_pdf_is_rejected(tmp_path: Path) -> None:
    document = fitz.open()
    document.new_page(width=595, height=842)
    source = tmp_path / "закрытый.pdf"
    document.save(
        source,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    document.close()

    with pytest.raises(ImportValidationError) as error:
        run(ProjectImportService(tmp_path / "projects").import_project(source))

    assert "парол" in str(error.value).lower()


def test_unsupported_format_message_lists_supported_sources(tmp_path: Path) -> None:
    source = tmp_path / "смета.txt"
    source.write_text("не проект", encoding="utf-8")

    with pytest.raises(UnsupportedSourceError) as error:
        run(ProjectImportService(tmp_path / "projects").import_project(source))

    assert "PDF" in str(error.value)
    assert "ПРО100" in str(error.value)


def test_pro100_export_directory_parses_details(tmp_path: Path) -> None:
    export = tmp_path / "Кухня"
    export.mkdir()
    (export / "details.txt").write_text(
        "\t600\t720\t16\t2\tЦвета\\Белый\n"
        "\t400\t500\t16\t1\tЦвета\\Серый\n"
        "ЛДСП\t390\t269\t16\t2\tЦвета\\Белый\n",
        encoding="utf-8",
    )
    (export / "estimate.txt").write_text("итого", encoding="utf-8")

    project = run(ProjectImportService(tmp_path / "projects").import_project(export))

    assert project.source_type == "pro100"
    assert project.metadata["parsed"] is True
    assert project.metadata["details_count"] == 3
    assert project.metadata["panels_count"] == 3
    assert project.metadata["details_path"].endswith("details.json")
    assert "details" not in project.metadata
    assert project.load_details()[0]["width_mm"] == 600
    assert project.pages == []
    assert len(project.source_files) == 2


def test_pro100_sto_is_preserved_but_not_parsed(tmp_path: Path) -> None:
    source = tmp_path / "Проект.sto"
    source.write_bytes(b"pro100-binary")

    project = run(ProjectImportService(tmp_path / "projects").import_project(source))

    assert project.source_type == "pro100"
    assert project.metadata["parsed"] is False
    assert project.metadata["format"] == "pro100_sto"
    assert project.pages == []
    assert any(".sto" in warning for warning in project.warnings)
    assert Path(project.source_files[0]).read_bytes() == b"pro100-binary"


def test_pro100_screenshot_is_kept_with_project(tmp_path: Path) -> None:
    export = tmp_path / "Кухня"
    export.mkdir()
    (export / "details.txt").write_text(
        "Полка\t400\t500\t16\t1\tЦвета\\Белый\n", encoding="utf-8"
    )
    Image.new("RGB", (40, 30), "white").save(export / "screenshot.png")

    project = run(ProjectImportService(tmp_path / "projects").import_project(export))

    assert project.metadata["screenshot"] is not None
    assert Path(project.metadata["screenshot"]).name == "screenshot.png"
    assert any(Path(item).name == "screenshot.png" for item in project.source_files)


def test_parse_details_line_handles_depth_and_empty_cells() -> None:
    with_depth = parse_details_line(
        "Боковина\t600\t720\t560\t16\t2\tЦвета\\Белый"
    )
    assert with_depth is not None
    assert with_depth["depth_mm"] == 560
    assert with_depth["width_mm"] == 600
    assert with_depth["quantity"] == 2
    assert with_depth["material"] == "Цвета\\Белый"

    no_depth = parse_details_line("Полка\t400\t500\t16\t1\tЦвета\\Серый")
    assert no_depth is not None
    assert no_depth["depth_mm"] is None
    assert no_depth["is_panel"] is True


def test_safe_filename_strips_directories() -> None:
    assert safe_filename("../../секрет.png") == "секрет.png"
    assert safe_filename("") == "source"
    assert safe_filename("CON") == "_CON"


def test_content_id_is_order_independent() -> None:
    assert content_id([b"a", b"b"]) == content_id([b"b", b"a"])
    assert content_id([b"a", b"b"]) != content_id([b"a", b"c"])
