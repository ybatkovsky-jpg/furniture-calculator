"""Импорт PDF: сохранение исходника, разбивка на страницы, извлечение картинок."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pymupdf as fitz
from PIL import Image

from app.services.importing.base import (
    BaseProjectImporter,
    ImportedPage,
    ImportedProject,
    ImportValidationError,
    ProjectStore,
    utc_now,
)
from app.services.pdf_renderer import render_page

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_RENDERED_PAGES = 100
MAX_EXTRACTED_IMAGES = 100


class PdfImporter(BaseProjectImporter):
    """PDF-альбом → проект со страницами.

    По умолчанию страницы не рендерятся при импорте: хранится только метаданные.
    Картинка создаётся по требованию через `ProjectImportService.ensure_page_image`
    — иначе импорт 100-страничного альбома занимал бы минуты и сотни мегабайт.
    """

    source_type = "pdf"

    def __init__(
        self,
        store: ProjectStore,
        *,
        max_bytes: int = MAX_PDF_BYTES,
        render_on_import: bool = False,
    ) -> None:
        super().__init__(store)
        self.max_bytes = max_bytes
        self.render_on_import = render_on_import

    def can_import(self, source: Path) -> bool:
        return source.is_file() and source.suffix.lower() == ".pdf"

    async def _payload(self, source: Path) -> bytes:
        size = source.stat().st_size
        if size <= 0:
            raise ImportValidationError(f"PDF пустой: {source.name}")
        if size > self.max_bytes:
            limit = self.max_bytes // (1024 * 1024)
            raise ImportValidationError(f"PDF больше {limit} МБ — разделите альбом")
        return source.read_bytes()

    async def _do_import(
        self,
        source: Path,
        project_id: str,
        payload: bytes,
    ) -> ImportedProject:
        saved = self.store.save_source(project_id, source, data=payload)
        warnings: list[str] = []

        try:
            document = fitz.open(str(saved))
        except (fitz.FileDataError, RuntimeError, ValueError) as exc:
            raise ImportValidationError(
                f"Не удалось открыть PDF «{source.name}»: {exc}"
            ) from exc

        try:
            if document.needs_pass:
                raise ImportValidationError(
                    "PDF защищён паролем — снимите защиту и загрузите снова"
                )
            page_count = document.page_count
            if page_count <= 0:
                raise ImportValidationError(f"В PDF «{source.name}» нет страниц")

            metadata = {
                "page_count": page_count,
                "title": (document.metadata or {}).get("title") or "",
                "author": (document.metadata or {}).get("author") or "",
                "subject": (document.metadata or {}).get("subject") or "",
                "rendered_pages": 0,
                "extracted_image_count": 0,
            }

            pages: list[ImportedPage] = []
            for index in range(page_count):
                width_mm, height_mm = _page_size_mm(document[index])
                page = ImportedPage(
                    index=index,
                    source_page=index + 1,
                    width_mm=width_mm,
                    height_mm=height_mm,
                )
                if self.render_on_import:
                    try:
                        jpeg = render_page(saved, index)
                        page_path = self.store.save_page(project_id, index, jpeg)
                        page.image_path = str(page_path)
                        with Image.open(io.BytesIO(jpeg)) as image:
                            page.width_px, page.height_px = image.size
                        metadata["rendered_pages"] += 1
                    except Exception as exc:  # noqa: BLE001 — одна страница не рушит импорт
                        logger.warning("Стр. %d не отрендерена: %s", index + 1, exc)
                        warnings.append(f"Страница {index + 1} не отрендерена: {exc}")
                pages.append(page)

            extracted = _extract_images(document, self.store, project_id, warnings)
            metadata["extracted_image_count"] = len(extracted)

            return ImportedProject(
                project_id=project_id,
                source_type=self.source_type,
                original_filename=source.name,
                source_files=[str(saved)],
                pages=pages,
                extracted_images=extracted,
                metadata=metadata,
                created_at=utc_now(),
                warnings=warnings,
            )
        finally:
            document.close()


def _page_size_mm(page: fitz.Page) -> tuple[float | None, float | None]:
    """Физический размер страницы в мм с учётом поворота."""
    try:
        rect = page.rect
        rotation = int(page.rotation or 0) % 360
    except Exception:  # noqa: BLE001 — размер не критичен для импорта
        return None, None
    width = rect.width
    height = rect.height
    if rotation in (90, 270):
        width, height = height, width
    return round(width * 25.4 / 72, 1), round(height * 25.4 / 72, 1)


def _extract_images(
    document: fitz.Document,
    store: ProjectStore,
    project_id: str,
    warnings: list[str],
) -> list[str]:
    """Извлечь встроенные изображения (визуализации, рендеры) без дублей."""
    extracted: list[str] = []
    seen_xrefs: set[int] = set()
    for page_index in range(document.page_count):
        if len(extracted) >= MAX_EXTRACTED_IMAGES:
            warnings.append(
                f"Извлечено только {MAX_EXTRACTED_IMAGES} изображений — лимит"
            )
            break
        try:
            images = document[page_index].get_images(full=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Стр. %d: не удалось перечислить картинки: %s", page_index + 1, exc)
            continue
        for image_number, info in enumerate(images, start=1):
            if len(extracted) >= MAX_EXTRACTED_IMAGES:
                break
            xref = int(info[0])
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            try:
                payload = document.extract_image(xref)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Xref %d не извлечён: %s", xref, exc)
                continue
            data = payload.get("image")
            extension = str(payload.get("ext") or "png").lower()
            if not data:
                continue
            name = f"page-{page_index + 1:03d}-img-{image_number:02d}.{extension}"
            try:
                path = store.save_image(project_id, name, data)
            except OSError as exc:
                warnings.append(f"Изображение {name} не сохранено: {exc}")
                continue
            extracted.append(str(path))
    return extracted
