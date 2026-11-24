"""Импорт растровых изображений: JPG, JPEG, PNG, WEBP, BMP."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.services.importing.base import (
    BaseProjectImporter,
    ImportedPage,
    ImportedProject,
    ImportValidationError,
    ProjectStore,
    utc_now,
)

MAX_IMAGE_BYTES = 20 * 1024 * 1024


class ImageImporter(BaseProjectImporter):
    """Сохраняет изображение как проект из одной страницы."""

    source_type = "image"
    SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp"})

    def __init__(self, store: ProjectStore, *, max_bytes: int = MAX_IMAGE_BYTES) -> None:
        super().__init__(store)
        self.max_bytes = max_bytes

    def can_import(self, source: Path) -> bool:
        return source.is_file() and source.suffix.lower() in self.SUFFIXES

    async def _payload(self, source: Path) -> bytes:
        size = source.stat().st_size
        if size <= 0:
            raise ImportValidationError(f"Файл пустой: {source.name}")
        if size > self.max_bytes:
            limit = self.max_bytes // (1024 * 1024)
            raise ImportValidationError(
                f"Изображение больше {limit} МБ — уменьшите файл или загрузите PDF"
            )
        return source.read_bytes()

    async def _do_import(
        self,
        source: Path,
        project_id: str,
        payload: bytes,
    ) -> ImportedProject:
        try:
            with Image.open(io.BytesIO(payload)) as probe:
                probe.verify()
            with Image.open(io.BytesIO(payload)) as image:
                width_px, height_px = image.size
                image_format = image.format or source.suffix.lstrip(".").upper()
                mode = image.mode
                dpi = image.info.get("dpi")
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ImportValidationError(
                f"Не удалось прочитать изображение «{source.name}»: {exc}"
            ) from exc

        width_mm, height_mm = _physical_size_mm(width_px, height_px, dpi)
        saved = self.store.save_source(project_id, source, data=payload)
        page = ImportedPage(
            index=0,
            image_path=str(saved),
            source_page=1,
            width_px=width_px,
            height_px=height_px,
            width_mm=width_mm,
            height_mm=height_mm,
        )
        return ImportedProject(
            project_id=project_id,
            source_type=self.source_type,
            original_filename=source.name,
            source_files=[str(saved)],
            pages=[page],
            extracted_images=[],
            metadata={
                "format": str(image_format).lower(),
                "mode": mode,
                "width_px": width_px,
                "height_px": height_px,
                "width_mm": width_mm,
                "height_mm": height_mm,
                "file_size": len(payload),
            },
            created_at=utc_now(),
        )


def _physical_size_mm(
    width_px: int,
    height_px: int,
    dpi: object,
) -> tuple[float | None, float | None]:
    """Физический размер по DPI, если он правдоподобен.

    Для чертежа, сфотографированного без метаданных, DPI ничего не значит —
    лучше вернуть None, чем выдать выдуманный размер.
    """
    if not isinstance(dpi, (tuple, list)) or len(dpi) < 2:
        return None, None
    try:
        x_dpi, y_dpi = float(dpi[0]), float(dpi[1])
    except (TypeError, ValueError):
        return None, None
    if not (1 <= x_dpi <= 2400 and 1 <= y_dpi <= 2400):
        return None, None
    return (
        round(width_px / x_dpi * 25.4, 1),
        round(height_px / y_dpi * 25.4, 1),
    )
