"""Резервная копия проектов импорта: data/projects → backups/.../projects_<ts>.

Запуск:
    .\\.venv\\Scripts\\python.exe scripts\\backup_projects.py
    .\\.venv\\Scripts\\python.exe scripts\\backup_projects.py --keep 7
"""

from __future__ import annotations

import argparse
import logging
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "data" / "projects"
DEFAULT_DEST = ROOT.parent / "backups" / "furniture-calculator"

logger = logging.getLogger(__name__)


def backup_projects(source: Path, dest: Path, keep: int = 7) -> Path:
    """Скопировать каталог проектов в датированную папку, старые — удалить."""
    if not source.is_dir():
        raise FileNotFoundError(f"Каталог проектов не найден: {source}")
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = dest / f"projects_{stamp}"
    shutil.copytree(source, target)
    logger.info("Копия проектов: %s", target)

    copies = sorted(dest.glob("projects_*"), key=lambda path: path.name)
    for old in copies[:-keep] if keep > 0 else []:
        shutil.rmtree(old, ignore_errors=True)
        logger.info("Удалена старая копия: %s", old)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Резервная копия data/projects")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        target = backup_projects(args.src, args.dest, args.keep)
    except (OSError, FileNotFoundError) as exc:
        logger.error("Копирование не удалось: %s", exc)
        return 1
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
