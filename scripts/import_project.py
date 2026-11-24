"""Импортировать проект в единое хранилище (этап 1).

Примеры:
    .\\.venv\\Scripts\\python.exe scripts\\import_project.py "pdfs\\альбом.pdf"
    .\\.venv\\Scripts\\python.exe scripts\\import_project.py "docs\\pro100_reports\\Кухня"
    .\\.venv\\Scripts\\python.exe scripts\\import_project.py "input_images\\лист.jpg"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.services.importing import ProjectImportError, ProjectImportService


async def _run(source: Path, storage: Path | None) -> int:
    service = ProjectImportService(storage)
    try:
        project = await service.import_project(source)
    except ProjectImportError as exc:
        print(f"Ошибка импорта: {exc}", file=sys.stderr)
        return 2

    summary = {
        "project_id": project.project_id,
        "source_type": project.source_type,
        "original_filename": project.original_filename,
        "storage_dir": project.storage_dir,
        "pages": len(project.pages),
        "rendered_pages": sum(1 for page in project.pages if page.image_path),
        "source_files": project.source_files,
        "extracted_images": len(project.extracted_images),
        "duplicate": project.is_duplicate,
        "warnings": project.warnings,
        "metadata": {
            key: value
            for key, value in project.metadata.items()
            if key != "details"
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Импорт проекта в единое хранилище")
    parser.add_argument("source", type=Path, help="JPG/PNG/PDF, details.txt, каталог или .sto")
    parser.add_argument(
        "--storage",
        type=Path,
        default=None,
        help="Каталог проектов (по умолчанию PROJECTS_DIR или data/projects)",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.source, args.storage))


if __name__ == "__main__":
    raise SystemExit(main())
