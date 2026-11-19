"""Разовая запись розничных ориентиров для позиций без цены (P2-19).

Решение собственника 17.11.2026: в прайс попадают только модельные позиции,
у которых цена не зависит от декора (ящики BOYARD, направляющие). Кромка,
столешницы и крашеный МДФ зависят от декора — они остаются без цены до счёта
поставщика.

Каждая запись помечается `metadata_json={"preliminary": true, "source": ...}`,
чтобы отличать розничный ориентир от закупочной цены. Скрипт идемпотентен:
повторный запуск не создаёт новых записей истории.

Запуск:
    .\\.venv\\Scripts\\python.exe scripts/apply_retail_prices.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DB_PATH = Path("data/furniture.db")
CHECKED_AT = "2026-11-17"

# name в прайсе → (цена, единица, источник, комментарий)
PRICES: dict[str, tuple[float, str, str, str]] = {
    "Стандартный ящик тонкий СТАРТ PUSH h=86 мм": (
        2270.0,
        "шт",
        "https://tomplit.ru/shop/goods/SB28_yaschik_start_PUSH_500mm_s_pryamyimi_bokovinami_h_86mm_grafit_k_t_SB28GRPH1_5000_-21684",
        "Розница SB28 PUSH 500 мм: 2 270 ₽; 400 мм — 2 020 ₽. Ориентир 2 020–2 270.",
    ),
    "Стандартный ящик тонкий СТАРТ PUSH h=116 мм": (
        2540.0,
        "шт",
        "https://tomplit.ru/shop/goods/SB29_yaschik_start_PUSH_450mm_s_pryamyimi_bokovinami_h_116mm_Ceryiy_k_t_SB29GR1_450_-21659",
        "Розница SB29 PUSH 450 мм: 2 540 ₽; 350 мм — 2 290 ₽, 500 мм — 2 640 ₽. Ориентир 2 290–2 640.",
    ),
    "Стандартный ящик тонкий СТАРТ PUSH h=167 мм": (
        2960.0,
        "шт",
        "https://tomplit.ru/shop/goods/SB30_yaschik_start_PUSH_450mm_s_pryamyimi_bokovinami_h_167mm_grafit_k_t_SB30GRPH1_450_-21579",
        "Розница SB30 PUSH 450 мм: 2 960 ₽; 300 мм — 2 530 ₽, 500 мм — 3 080 ₽. Ориентир 2 530–3 080.",
    ),
    "BOYARD С ДОВОДЧИКОМ 3D полного выдвижения": (
        1675.0,
        "шт",
        "https://tomplit.ru/shop/goods/DB9991Zn_500_napravlyayuschie_skryitogo_montaja_s_3D_regulirovkoy_i_sinhronizatsiey_6_-19444",
        "Розница DB9991: 500 мм — 1 675 ₽, 300 мм — 1 320 ₽. Ориентир 1 320–1 675.",
    ),
}


def main(dry_run: bool = False) -> int:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    cursor = connection.cursor()

    updated = 0
    for name, (price, unit, source, note) in PRICES.items():
        row = cursor.execute(
            "select id, unit_price, price_unit from price_items "
            "where name = ? and is_active = 1",
            (name,),
        ).fetchone()
        if row is None:
            print(f"НЕ НАЙДЕНО: {name}")
            continue
        if row["unit_price"] is not None and float(row["unit_price"]) == price:
            print(f"УЖЕ ЕСТЬ: {name} — {price:g} ₽")
            continue

        metadata = {
            "preliminary": True,
            "source": source,
            "checked_at": CHECKED_AT,
            "note": note,
        }
        print(f"ЗАПИСЬ: {name} — {price:g} ₽/{unit} (было {row['unit_price']})")
        updated += 1
        if dry_run:
            continue

        cursor.execute(
            "update price_items set unit_price = ?, price_unit = ?, "
            "requires_manual_price = 0, description = ?, metadata_json = ?, "
            "updated_at = ? where id = ?",
            (
                price,
                unit,
                f"Розничный ориентир от {CHECKED_AT}, подтвердить у поставщика. {note}",
                json.dumps(metadata, ensure_ascii=False),
                datetime.now(UTC).isoformat(sep=" ", timespec="seconds"),
                row["id"],
            ),
        )
        cursor.execute(
            "insert into price_history (item_id, old_price, new_price, changed_by, "
            "change_reason, changed_at) values (?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                row["unit_price"],
                price,
                "audit-2026-11-17",
                "розничный ориентир (предварительно)",
                datetime.now(UTC).isoformat(sep=" ", timespec="seconds"),
            ),
        )

    if dry_run:
        connection.rollback()
        print(f"DRY-RUN: обновлено бы {updated} позиций")
    else:
        connection.commit()
        print(f"Обновлено позиций: {updated}")

    left = cursor.execute(
        "select count(*) from price_items where is_active = 1 "
        "and (unit_price is null or unit_price <= 0 or requires_manual_price = 1)"
    ).fetchone()[0]
    print(f"Осталось активных без цены: {left}")
    connection.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(main(dry_run=args.dry_run))
