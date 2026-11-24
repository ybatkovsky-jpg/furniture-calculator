"""Единая точка импорта проекта: выбор импортёра по источнику."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image

from app.config import settings
from app.services.importing.base import (
    BaseProjectImporter,
    ImportedPage,
    ImportedProject,
    ImportValidationError,
    ProjectImportError,
    ProjectStore,
    UnsupportedSourceError,
)
from app.services.importing.image_importer import ImageImporter
from app.services.importing.pdf_importer import PdfImporter
from app.services.importing.pro100_importer import Pro100Importer
from app.services.pdf_renderer import render_page

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]


class ProjectImportService:
    """Принять JPG/PNG/WEBP/BMP, PDF или выгрузку ПРО100."""

    def __init__(self, storage_root: str | Path | None = None) -> None:
        root = Path(storage_root) if storage_root is not None else _default_root()
        self.store = ProjectStore(root)
        self.importers: list[BaseProjectImporter] = [
            ImageImporter(
                self.store,
                max_bytes=settings.import_max_image_mb * 1024 * 1024,
            ),
            PdfImporter(
                self.store,
                max_bytes=settings.import_max_pdf_mb * 1024 * 1024,
            ),
            Pro100Importer(self.store),
        ]

    def ensure_page_image(self, project_id: str, page_index: int) -> ImportedPage:
        """Отрендерить страницу PDF по требованию и обновить манифест."""
        project = self.store.load(project_id)
        if project is None:
            raise ImportValidationError("Проект не найден")
        if page_index < 0 or page_index >= len(project.pages):
            raise ImportValidationError("Страница не найдена")
        page = project.pages[page_index]
        if page.image_path and Path(page.image_path).is_file():
            return page
        if project.source_type != "pdf":
            raise ImportValidationError("У этого проекта нет PDF-страниц для рендера")

        source = next(
            (
                Path(item)
                for item in project.source_files
                if Path(item).suffix.lower() == ".pdf"
            ),
            None,
        )
        if source is None or not source.is_file():
            raise ImportValidationError("Исходный PDF проекта не найден")

        pdf_page = max(0, int(page.source_page or page.index + 1) - 1)
        try:
            jpeg = render_page(source, pdf_page)
        except Exception as exc:
            logger.exception("Страница %d не отрендерена", page_index + 1)
            raise ImportValidationError(
                f"Не удалось отрендерить страницу {page_index + 1}: {exc}"
            ) from exc

        saved = self.store.save_page(project.project_id, page.index, jpeg)
        page.image_path = str(saved)
        with Image.open(io.BytesIO(jpeg)) as image:
            page.width_px, page.height_px = image.size
        self.store.save_manifest(project)
        return page

    async def import_project(self, source: str | Path) -> ImportedProject:
        """Импортировать источник; ошибки говорят менеджеру, что делать."""
        path = Path(source).expanduser()
        if not path.exists():
            raise ImportValidationError(f"Источник не найден: {path}")

        for importer in self.importers:
            if not importer.can_import(path):
                continue
            try:
                return await importer.import_project(path)
            except ProjectImportError:
                raise
            except Exception as exc:
                logger.exception("Импорт %s упал", path)
                raise ProjectImportError(
                    f"Не удалось импортировать «{path.name}»: {exc}"
                ) from exc

        raise UnsupportedSourceError(
            "Поддерживаются JPG, JPEG, PNG, WEBP, BMP, PDF и выгрузка ПРО100 "
            "(details.txt или .sto). Выберите файл одного из этих форматов."
        )


def _default_root() -> Path:
    configured = Path(settings.projects_dir or "data/projects")
    if configured.is_absolute():
        return configured
    return REPO_ROOT / configured
