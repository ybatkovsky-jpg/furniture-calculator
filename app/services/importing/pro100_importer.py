"""Импорт выгрузки ПРО100.

Поддержаны два реально проверенных источника:

* каталог экспорта со `details.txt` (скрипты `scripts/pro100_export.py` /
  `scripts/pro100_batch.py`): разбираем строки деталировки;
* файл проекта `.sto`: сохраняем как исходник, но **не** заявляем разбор
  закрытого формата — в метаданных явно `parsed=false`.

Формат `.sto` не документирован; его структуру нельзя считать поддержанной,
пока не появится проверенный парсер.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.services.importing.base import (
    BaseProjectImporter,
    ImportedProject,
    ImportValidationError,
    utc_now,
)

logger = logging.getLogger(__name__)

MAX_PRO100_FILE_BYTES = 20 * 1024 * 1024
EXPORT_TEXT_FILES = ("details.txt", "elements.txt", "estimate.txt", "materials.txt")


def parse_details_line(line: str) -> dict[str, Any] | None:
    """Разобрать строку «Списка деталей» ПРО100.

    Формат: `[название] W H [D] толщина количество [материал]`, разделители —
    табы; в реальных выгрузках встречаются `=` и `—` как пустые колонки.
    """
    parts = [part.strip() for part in (line or "").split("\t")]
    tokens = [part for part in parts if part]
    if len(tokens) < 4:
        return None

    material = ""
    last = tokens[-1]
    if any(ch.isalpha() for ch in last) or "\\" in last:
        material = last
        tokens = tokens[:-1]

    numbers: list[float] = []
    for token in tokens:
        try:
            numbers.append(float(token.replace(",", ".")))
        except ValueError:
            continue
    if len(numbers) < 4:
        return None

    if len(numbers) >= 5:
        width, height, depth, thickness, quantity = numbers[-5:]
    else:
        width, height, depth, thickness, quantity = (
            numbers[-4], numbers[-3], None, numbers[-2], numbers[-1],
        )

    name = ""
    for token in tokens:
        if token in {"=", "—", "-"}:
            continue
        try:
            float(token.replace(",", "."))
        except ValueError:
            name = token
            break

    is_panel = (
        2 <= thickness <= 60
        and 0 < width <= 3200
        and 0 < height <= 3200
        and not material.startswith("Покрытия")
    )
    return {
        "name": name,
        "width_mm": _number(width),
        "height_mm": _number(height),
        "depth_mm": _number(depth) if depth is not None else None,
        "thickness_mm": _number(thickness),
        "quantity": max(1, round(quantity)),
        "material": material,
        "is_panel": is_panel,
    }


def _number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(value, 2)


class Pro100Importer(BaseProjectImporter):
    """Импорт выгрузки ПРО100 (details.txt / каталог экспорта / .sto)."""

    source_type = "pro100"

    def can_import(self, source: Path) -> bool:
        if source.is_dir():
            return _find_details(source) is not None
        if not source.is_file():
            return False
        if source.suffix.lower() == ".sto":
            return True
        return source.name.lower() == "details.txt"

    async def _payload(self, source: Path) -> bytes:
        if source.is_dir():
            chunks = []
            for path in _export_files(source):
                try:
                    chunks.append(path.read_bytes())
                except OSError as exc:
                    raise ImportValidationError(
                        f"Не удалось прочитать «{path.name}»: {exc}"
                    ) from exc
            if not chunks:
                raise ImportValidationError("В каталоге экспорта нет файлов")
            return b"\n".join(chunks)
        size = source.stat().st_size
        if size <= 0:
            raise ImportValidationError(f"Файл ПРО100 пустой: {source.name}")
        if size > MAX_PRO100_FILE_BYTES:
            raise ImportValidationError(
                f"Файл ПРО100 больше {MAX_PRO100_FILE_BYTES // (1024 * 1024)} МБ"
            )
        return source.read_bytes()

    async def _do_import(
        self,
        source: Path,
        project_id: str,
        payload: bytes,
    ) -> ImportedProject:
        warnings: list[str] = []
        source_files: list[str] = []

        if source.is_file() and source.suffix.lower() == ".sto":
            saved = self.store.save_source(project_id, source, data=payload)
            warnings.append(
                "Файл .sto сохранён, но его формат не разобран. Для расчёта "
                "выгрузите «Список деталей» (details.txt) средствами ПРО100."
            )
            return ImportedProject(
                project_id=project_id,
                source_type=self.source_type,
                original_filename=source.name,
                source_files=[str(saved)],
                pages=[],
                extracted_images=[],
                metadata={
                    "format": "pro100_sto",
                    "parsed": False,
                    "file_size": len(payload),
                },
                created_at=utc_now(),
                warnings=warnings,
            )

        if source.is_dir():
            original_filename = source.name
            details_path = _find_details(source)
            files = _export_files(source)
        else:
            original_filename = source.parent.name or source.name
            details_path = source
            files = [source]

        if details_path is None:
            raise ImportValidationError(
                "В каталоге ПРО100 нет details.txt — выгрузите «Список деталей»"
            )

        details_data = payload if source.is_file() else details_path.read_bytes()
        for path in files:
            try:
                data = details_data if path == details_path else path.read_bytes()
                saved = self.store.save_source(
                    project_id, path, data=data, filename=path.name
                )
            except OSError as exc:
                warnings.append(f"Файл «{path.name}» не сохранён: {exc}")
                continue
            source_files.append(str(saved))

        rows = _parse_details(details_data.decode("utf-8", errors="replace"))
        panels = [row for row in rows if row["is_panel"]]
        if not rows:
            warnings.append(
                "В details.txt не найдено ни одной строки детали — проверьте выгрузку"
            )
        details_file = self.store.save_json(project_id, "details.json", rows)
        screenshot = next(
            (
                str(saved)
                for saved in source_files
                if Path(saved).name.lower() in {"screenshot.png", "screenshot.jpg"}
            ),
            None,
        )
        metadata = {
            "format": "pro100_export",
            "parsed": True,
            "details_count": len(rows),
            "panels_count": len(panels),
            "details_path": str(details_file),
            "screenshot": screenshot,
            "source_filename": details_path.name,
        }
        return ImportedProject(
            project_id=project_id,
            source_type=self.source_type,
            original_filename=original_filename,
            source_files=source_files,
            pages=[],
            extracted_images=[],
            metadata=metadata,
            created_at=utc_now(),
            warnings=warnings,
        )


def _find_details(directory: Path) -> Path | None:
    for path in directory.iterdir():
        if path.is_file() and path.name.lower() == "details.txt":
            return path
    return None


def _export_files(directory: Path) -> list[Path]:
    """Файлы экспорта ПРО100: известные текстовые отчёты и проект .sto."""
    files: list[Path] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        if (
            path.name.lower() in EXPORT_TEXT_FILES
            or path.suffix.lower() == ".sto"
            or path.name.lower() in {"screenshot.png", "screenshot.jpg"}
        ) and path.stat().st_size <= MAX_PRO100_FILE_BYTES:
            files.append(path)
    return files


def _parse_details(text: str) -> list[dict[str, Any]]:
    rows = []
    for line in text.splitlines():
        parsed = parse_details_line(line.lstrip("\ufeff"))
        if parsed is not None:
            rows.append(parsed)
    return rows
