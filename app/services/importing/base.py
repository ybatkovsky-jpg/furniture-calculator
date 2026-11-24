"""Базовые сущности единого импорта проекта (этап 1).

Все источники (изображение, PDF, выгрузка ПРО100) приводятся к одному
`ImportedProject`. Дальше по конвейеру исходный формат уже не важен: распознавание,
BOM и расчёт работают с проектом и его страницами.

Пакет называется `importing`, а не `import`: `import` — ключевое слово Python,
пакет с таким именем нельзя импортировать обычным `from ... import ...`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

MANIFEST_NAME = "project.json"
MAX_SOURCE_NAME_LENGTH = 120
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class ProjectImportError(Exception):
    """Ошибка импорта с сообщением, понятным менеджеру."""


class UnsupportedSourceError(ProjectImportError):
    """Формат источника не поддерживается."""


class ImportValidationError(ProjectImportError):
    """Источник повреждён или не прошёл проверку."""


@dataclass(slots=True)
class ImportedPage:
    """Одна страница проекта.

    `image_path` — готовый к распознаванию файл. Для PDF это рендер страницы,
    для изображения — сохранённый исходник. `None` означает, что страница
    известна, но не отрендерена (лимит или ошибка рендера).
    """

    index: int
    image_path: str | None = None
    source_page: int | None = None
    width_px: int | None = None
    height_px: int | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    # Этап 2, модель B: помещение и проекция живут на странице, модули
    # наследуют их при сборке RoomSpec. Для многоракурсных листов
    # view_group позволяет не суммировать одну и ту же мебель дважды.
    room_id: str | None = None
    room_name: str | None = None
    view_type: str | None = None  # plan | elevation | section | render | detail
    view_group: str | None = None


@dataclass(slots=True)
class ImportedProject:
    """Единый результат импорта любого источника."""

    project_id: str
    source_type: str
    original_filename: str
    source_files: list[str] = field(default_factory=list)
    pages: list[ImportedPage] = field(default_factory=list)
    extracted_images: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    storage_dir: str = ""
    warnings: list[str] = field(default_factory=list)
    is_duplicate: bool = False
    status: str = "IMPORTED"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pages"] = [asdict(page) for page in self.pages]
        return data

    def load_details(self) -> list[dict[str, Any]]:
        """Строки деталировки: из details.json, старый манифест — из metadata."""
        path = self.metadata.get("details_path")
        if path:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except (OSError, ValueError):
                logger.warning("details.json не прочитан: %s", path)
        legacy = self.metadata.get("details")
        return list(legacy) if isinstance(legacy, list) else []

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImportedProject:
        pages = [
            ImportedPage(**page) if isinstance(page, dict) else page
            for page in data.get("pages") or []
        ]
        return cls(
            project_id=str(data["project_id"]),
            source_type=str(data["source_type"]),
            original_filename=str(data.get("original_filename") or ""),
            source_files=[str(item) for item in data.get("source_files") or []],
            pages=pages,
            extracted_images=[str(item) for item in data.get("extracted_images") or []],
            metadata=dict(data.get("metadata") or {}),
            created_at=str(data.get("created_at") or ""),
            storage_dir=str(data.get("storage_dir") or ""),
            warnings=[str(item) for item in data.get("warnings") or []],
            status=str(data.get("status") or "IMPORTED"),
        )


def utc_now() -> str:
    """Текущее время в ISO-формате UTC."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def safe_filename(name: str, *, fallback: str = "source") -> str:
    """Безопасное имя файла без каталогов и запрещённых символов Windows."""
    base = Path(str(name or "")).name.replace("\\", "_")
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
    if not base:
        base = fallback
    stem, dot, suffix = base.rpartition(".")
    if not dot:
        stem, suffix = base, ""
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    if len(stem) > MAX_SOURCE_NAME_LENGTH:
        stem = stem[:MAX_SOURCE_NAME_LENGTH]
    return f"{stem}.{suffix}" if suffix else stem


def content_id(chunks: list[bytes]) -> str:
    """Стабильный ID проекта по содержимому источников.

    Одинаковый файл, загруженный повторно, получает тот же ID — дубликаты
    не создаются. Для набора файлов порядок не важен: хэши сортируются.
    """
    digests = sorted(hashlib.sha256(chunk).digest() for chunk in chunks if chunk)
    if not digests:
        raise ImportValidationError("Пустой источник: нечего импортировать")
    return hashlib.sha256(b"".join(digests)).hexdigest()[:16]


class ProjectStore:
    """Хранилище проектов на диске: исходники, страницы, манифест."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def project_dir(self, project_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{16}", project_id):
            raise ImportValidationError("Некорректный идентификатор проекта")
        return self.root / project_id

    def manifest_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / MANIFEST_NAME

    def load(self, project_id: str) -> ImportedProject | None:
        """Прочитать манифест; повреждённый манифест считается отсутствующим."""
        path = self.manifest_path(project_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            project = ImportedProject.from_dict(data)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Манифест %s повреждён: %s", path, exc)
            return None
        if not project.storage_dir:
            project.storage_dir = str(path.parent)
        return project

    def save_manifest(self, project: ImportedProject) -> Path:
        path = self.manifest_path(project.project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Уникальное имя: две параллельные загрузки одного проекта не должны
        # перезаписывать один и тот же .tmp и ломать replace().
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temp_path.write_text(
            json.dumps(project.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)
        return path

    def save_source(
        self,
        project_id: str,
        source: str | Path,
        *,
        data: bytes | None = None,
        filename: str | None = None,
    ) -> Path:
        """Сохранить исходник, не перезаписывая файл с другим содержимым."""
        source_path = Path(source)
        payload = data if data is not None else source_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        target_dir = self.project_dir(project_id) / "source"
        target_dir.mkdir(parents=True, exist_ok=True)

        name = safe_filename(filename or source_path.name)
        target = target_dir / name
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                return target
            candidate = target_dir / f"{target.stem}-{digest[:8]}{target.suffix}"
            if candidate.exists() and hashlib.sha256(
                candidate.read_bytes()
            ).hexdigest() == digest:
                return candidate
            target = candidate

        target.write_bytes(payload)
        return target

    def save_page(
        self,
        project_id: str,
        page_index: int,
        data: bytes,
        *,
        suffix: str = ".jpg",
    ) -> Path:
        target_dir = self.project_dir(project_id) / "pages"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"page-{page_index + 1:03d}{suffix}"
        target.write_bytes(data)
        return target

    def save_image(
        self,
        project_id: str,
        name: str,
        data: bytes,
    ) -> Path:
        target_dir = self.project_dir(project_id) / "images"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_filename(name)
        target.write_bytes(data)
        return target

    def save_json(self, project_id: str, name: str, data: Any) -> Path:
        """Сохранить побочный JSON (например, details.json) атомарно."""
        target = self.project_dir(project_id) / safe_filename(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(target)
        return target


class BaseProjectImporter(ABC):
    """Общий каркас импортёра: проверка, дедупликация, манифест."""

    source_type: ClassVar[str] = "unknown"

    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    @abstractmethod
    def can_import(self, source: Path) -> bool:
        """Может ли импортёр обработать этот источник."""

    @abstractmethod
    async def _payload(self, source: Path) -> bytes:
        """Байты для стабильного ID проекта (не обязательно весь файл)."""

    @abstractmethod
    async def _do_import(
        self,
        source: Path,
        project_id: str,
        payload: bytes,
    ) -> ImportedProject:
        """Скопировать источник и построить ImportedProject."""

    async def import_project(self, source: str | Path) -> ImportedProject:
        """Импортировать источник; повторная загрузка вернёт тот же проект."""
        path = Path(source).expanduser()
        if not path.exists():
            raise ImportValidationError(f"Источник не найден: {path}")
        if not self.can_import(path):
            raise UnsupportedSourceError(
                f"Формат «{path.suffix or path.name}» не поддерживается"
            )

        payload = await self._payload(path)
        project_id = content_id([payload])
        existing = self.store.load(project_id)
        if existing is not None:
            existing.is_duplicate = True
            logger.info("Проект %s уже импортирован — возвращаю существующий", project_id)
            return existing

        project = await self._do_import(path, project_id, payload)
        project.project_id = project_id
        project.storage_dir = str(self.store.project_dir(project_id))
        project.created_at = project.created_at or utc_now()
        self.store.save_manifest(project)
        logger.info(
            "Импортирован проект %s (%s): %d страниц",
            project_id,
            project.source_type,
            len(project.pages),
        )
        return project
