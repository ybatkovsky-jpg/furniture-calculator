"""Нормализация старых позиций прайса после бага P0-01 (dry-run по умолчанию).

Старый импорт классифицировал кромку как ЛДСП/МДФ и иногда склеивал цифры
названия в цену («0,4*19» → 0,419 ₽). Скрипт находит позиции вида
«<бренд> 0,4*19 / 0,8*19 / 2*19 / 1*22», переводит их в категорию «Кромка»,
проставляет толщину/единицу и обнуляет явно битую цену (< 1 ₽), помечая
позицию как «требует ручной цены».

Запуск:
    .\\.venv\\Scripts\\python.exe scripts\\normalize_legacy_price.py
    .\\.venv\\Scripts\\python.exe scripts\\normalize_legacy_price.py --apply
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path("data") / "furniture.db"
THICKNESS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[*×x]\s*(\d+)")
EDGE_WIDTHS = {19, 22, 23, 35}


def find_candidates(db_path: Path) -> list[tuple]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT id, name, category, unit_price, price_unit, thickness_mm "
            "FROM price_items WHERE is_active=1"
        ).fetchall()
    finally:
        con.close()

    result = []
    for item_id, name, category, price, unit, thickness in rows:
        match = THICKNESS_RE.search(name or "")
        if not match:
            continue
        try:
            thickness_mm = float(match.group(1).replace(",", "."))
            width_mm = int(match.group(2))
        except ValueError:
            continue
        if width_mm not in EDGE_WIDTHS or thickness_mm > 3:
            continue
        if category in ("ЛДСП", "МДФ", "МДФ плиты", "ХДФ"):
            result.append((item_id, name, category, price, unit, thickness_mm))
    return result


def apply_fix(db_path: Path, candidates: list[tuple]) -> int:
    con = sqlite3.connect(db_path)
    changed = 0
    try:
        for item_id, _name, _category, price, _unit, thickness in candidates:
            broken_price = price is None or float(price) < 1
            con.execute(
                "UPDATE price_items SET category='Кромка', price_unit='м', "
                "thickness_mm=?, unit_price=?, requires_manual_price=? "
                "WHERE id=?",
                (
                    thickness,
                    None if broken_price else price,
                    1 if broken_price else 0,
                    item_id,
                ),
            )
            changed += 1
        con.commit()
    finally:
        con.close()
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Нормализация старых позиций кромки")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--apply", action="store_true", help="применить изменения")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"База не найдена: {db_path}", file=sys.stderr)
        return 1

    candidates = find_candidates(db_path)
    if not candidates:
        print("Позиций для нормализации не найдено.")
        return 0

    print(f"Кандидатов: {len(candidates)}")
    for item_id, name, category, price, unit, thickness in candidates:
        print(
            f"  id={item_id} {category} → Кромка: {name} — "
            f"{price} ₽/{unit}, толщина {thickness:g} мм"
        )

    if args.apply:
        print(f"Обновлено позиций: {apply_fix(db_path, candidates)}")
    else:
        print("Режим проверки. Для применения добавьте --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
