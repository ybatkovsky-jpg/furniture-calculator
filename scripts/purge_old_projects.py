"""Удаление устаревших проектов с ПДн (P2-21).

По умолчанию — только показать, что будет удалено. Удаление требует явного
`--apply` и предварительного бэкапа БД (`scripts/backup_db.py`).

Запуск:
    python scripts/purge_old_projects.py --days 1825
    python scripts/purge_old_projects.py --days 1825 --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_DB = Path("data") / "furniture.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Удаление старых проектов с ПДн")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--days", type=int, required=True, help="Срок хранения, дней")
    parser.add_argument("--apply", action="store_true", help="выполнить удаление")
    args = parser.parse_args(argv)

    if args.days <= 0:
        print("--days должен быть положительным", file=sys.stderr)
        return 1
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"База не найдена: {db_path}", file=sys.stderr)
        return 1

    cutoff = (datetime.utcnow() - timedelta(days=args.days)).isoformat(sep=" ")
    con = sqlite3.connect(db_path)
    try:
        projects = con.execute(
            "SELECT id, created_at, client_name FROM projects "
            "WHERE created_at IS NOT NULL AND created_at < ?",
            (cutoff,),
        ).fetchall()
        if not projects:
            print(f"Проектов старше {args.days} дней нет (cutoff {cutoff}).")
            return 0

        print(f"Проектов к удалению: {len(projects)} (cutoff {cutoff})")
        total_calcs = 0
        for project_id, created_at, client_name in projects:
            calcs = con.execute(
                "SELECT count(*) FROM calculations WHERE project_id=?",
                (project_id,),
            ).fetchone()[0]
            total_calcs += calcs
            print(f"  #{project_id} {created_at} расчётов={calcs}")

        if not args.apply:
            print("Режим проверки. Для удаления добавьте --apply после бэкапа.")
            return 0

        for project_id, *_ in projects:
            con.execute(
                "DELETE FROM commercial_offers WHERE calculation_id IN "
                "(SELECT id FROM calculations WHERE project_id=?)",
                (project_id,),
            )
            con.execute("DELETE FROM calculations WHERE project_id=?", (project_id,))
            con.execute("DELETE FROM projects WHERE id=?", (project_id,))
        con.commit()
        print(f"Удалено проектов: {len(projects)}, расчётов: {total_calcs}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
