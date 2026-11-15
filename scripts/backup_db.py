"""Резервное копирование SQLite-базы (P1-15a, этап 0).

Делает консистентную копию через `sqlite3.Connection.backup` (а не копирование
файла на работающей БД), проверяет `PRAGMA integrity_check`, кладёт копию вне
репозитория и чистит старые архивы.

Запуск (из корня проекта):
    .\\.venv\\Scripts\\python.exe scripts\\backup_db.py
    .\\.venv\\Scripts\\python.exe scripts\\backup_db.py --db data\\furniture.db --keep 14

Каталог назначения по умолчанию — `..\\backups\\furniture-calculator` (вне Git и
Docker-контекста). Переопределяется `--dest` или `BACKUP_DIR`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("backup_db")

DEFAULT_DB = Path("data") / "furniture.db"
DEFAULT_DEST = Path("..") / "backups" / "furniture-calculator"


def backup_database(db_path: Path, dest_dir: Path, keep_days: int = 14) -> Path:
    """Сделать проверенную копию БД и вернуть путь к ней."""
    if not db_path.exists():
        raise FileNotFoundError(f"База не найдена: {db_path.resolve()}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = dest_dir / f"{db_path.stem}_{stamp}.db"

    source = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    try:
        check = source.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise RuntimeError(f"integrity_check исходной БД: {check}")
        target_conn = sqlite3.connect(target)
        try:
            source.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source.close()

    verify = sqlite3.connect(f"file:{target.resolve()}?mode=ro", uri=True)
    try:
        check = verify.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise RuntimeError(f"integrity_check копии: {check}")
        tables = verify.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    finally:
        verify.close()

    logger.info("Копия создана: %s (таблиц: %s, integrity_check=ok)", target, tables)
    purge_old(dest_dir, db_path.stem, keep_days)
    return target


def purge_old(dest_dir: Path, stem: str, keep_days: int) -> int:
    """Удалить копии старше `keep_days`; вернуть количество удалённых."""
    if keep_days <= 0:
        return 0
    cutoff = datetime.now() - timedelta(days=keep_days)
    removed = 0
    for path in dest_dir.glob(f"{stem}_*.db"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue
        if mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    if removed:
        logger.info("Удалено старых копий: %s (старше %s дн.)", removed, keep_days)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Резервное копирование SQLite-базы")
    parser.add_argument(
        "--db",
        default=os.environ.get("DB_PATH", str(DEFAULT_DB)),
        help="Путь к SQLite-базе (по умолчанию data/furniture.db)",
    )
    parser.add_argument(
        "--dest",
        default=os.environ.get("BACKUP_DIR", str(DEFAULT_DEST)),
        help="Каталог для копий (по умолчанию вне репозитория)",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=int(os.environ.get("BACKUP_KEEP_DAYS", "14")),
        help="Сколько дней хранить копии (по умолчанию 14)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        target = backup_database(Path(args.db), Path(args.dest), args.keep)
    except Exception as exc:  # noqa: BLE001 — CLI-скрипт: печатаем причину и код 1
        logger.error("Резервное копирование не выполнено: %s", exc)
        return 1
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
