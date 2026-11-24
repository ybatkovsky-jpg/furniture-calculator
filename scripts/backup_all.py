"""Одна команда для backup: SQLite + data/projects (этап 1D).

Запуск:
    .\\.venv\\Scripts\\python.exe scripts\\backup_all.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from backup_db import backup_database
from backup_projects import backup_projects

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "furniture.db"
DEFAULT_PROJECTS = ROOT / "data" / "projects"
DEFAULT_DEST = ROOT.parent / "backups" / "furniture-calculator"


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup БД и проектов импорта")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--projects", type=Path, default=DEFAULT_PROJECTS)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--keep-db", type=int, default=14)
    parser.add_argument("--keep-projects", type=int, default=7)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        db_copy = backup_database(args.db, args.dest, args.keep_db)
        print(f"БД: {db_copy}")
        if args.projects.is_dir():
            projects_copy = backup_projects(
                args.projects, args.dest, args.keep_projects
            )
            print(f"Проекты: {projects_copy}")
        else:
            print(f"Проекты: каталог {args.projects} отсутствует, пропущено")
    except (OSError, RuntimeError) as exc:
        print(f"Backup не выполнен: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
