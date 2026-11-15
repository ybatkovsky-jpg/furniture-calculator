"""Санитарная проверка прайса: явно битые цены (P0-01).

Ищет активные позиции с подозрительно низкой ценой для своей категории:
- ЛДСП/МДФ/ХДФ: цена листа меньше 100 ₽;
- Кромка: меньше 1 ₽/м;
- любые позиции: цена ≤ 0.

По умолчанию только показывает. `--apply` переводит такие позиции в
`requires_manual_price=1` (unit_price=NULL), чтобы они не участвовали в
расчёте до ручного подтверждения. Данные не удаляются.

Запуск:
    .\\.venv\\Scripts\\python.exe scripts\\check_price_sanity.py
    .\\.venv\\Scripts\\python.exe scripts\\check_price_sanity.py --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path("data") / "furniture.db"

THRESHOLDS = {
    "ЛДСП": 100.0,
    "МДФ": 100.0,
    "МДФ плиты": 100.0,
    "ХДФ": 100.0,
    "Кромка": 1.0,
}


def find_suspicious(db_path: Path) -> list[tuple]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT id, name, category, unit_price, price_unit "
            "FROM price_items WHERE is_active=1 AND unit_price IS NOT NULL"
        ).fetchall()
    finally:
        con.close()

    suspicious = []
    for item_id, name, category, price, unit in rows:
        threshold = THRESHOLDS.get(category)
        if price is None:
            continue
        if price <= 0 or (threshold is not None and price < threshold):
            suspicious.append((item_id, name, category, price, unit))
    return suspicious


def apply_fix(db_path: Path, items: list[tuple]) -> int:
    con = sqlite3.connect(db_path)
    try:
        for item_id, *_ in items:
            con.execute(
                "UPDATE price_items SET unit_price=NULL, requires_manual_price=1 "
                "WHERE id=?",
                (item_id,),
            )
        con.commit()
    finally:
        con.close()
    return len(items)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка битых цен в прайсе")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="обнулить битые цены и пометить requires_manual_price",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"База не найдена: {db_path}", file=sys.stderr)
        return 1

    items = find_suspicious(db_path)
    if not items:
        print("Подозрительных цен не найдено.")
        return 0

    print(f"Подозрительных позиций: {len(items)}")
    for item_id, name, category, price, unit in items:
        print(f"  id={item_id} {category}: {name} — {price} ₽/{unit}")

    if args.apply:
        changed = apply_fix(db_path, items)
        print(f"Обнулено и помечено «требует ручной цены»: {changed}")
    else:
        print("Режим проверки. Для исправления добавьте --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
