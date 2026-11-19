"""Сверка цены по xlsx-расчётам Olga_project — по компонентам.

Их блок: СЕБЕСТОИМОСТЬ → ИЗГОТОВЛЕНИЕ → МОНТАЖ → ЗАМЕР → ДОСТАВКА →
ПРОЕКТИРОВКА → ПРОЧИЕ → ИТОГО → ПЛАНИРУЕМАЯ ПРИБЫЛЬ → ИТОГО →
ТЕХДИРЕКТОР → ИТОГО за наличку → безнал.

Считаем их фактические ставки (компонент / себестоимость), сравниваем с нашими
и пересчитываем наличку нашими коэффициентами.

Запуск: .\\.venv\\Scripts\\python.exe scripts/excel_price_reconcile.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

SCAN = Path("docs/olga_coef_scan.json")
OUT = Path("docs/excel_price_reconcile.json")

OURS = {
    "manufacturing": 0.80,
    "install": 0.15,
    "design": 0.08,
    "other": 0.05,
    "profit": 0.35,
    "tech": 0.02,
}
NONCASH = 1.10
FIXED = ("measure", "delivery")


def load_sheets() -> list[dict]:
    data = json.loads(SCAN.read_text(encoding="utf-8"))
    sheets = []
    for file_row in data:
        grouped: dict[str, dict] = {}
        for row in file_row["rows"]:
            sheet = grouped.setdefault(
                row["sheet"], {"file": row["file"], "sheet": row["sheet"]}
            )
            key = row["key"]
            # «ИТОГО» встречается дважды: до прибыли и после. Берём первое.
            if key == "total1" and "subtotal_row" not in sheet:
                sheet["subtotal_row"] = row["value"]
                continue
            sheet[key] = row["value"]
        for sheet in grouped.values():
            if sheet.get("cost", 0) > 0:
                sheets.append(sheet)
    return sheets


def main() -> int:
    sheets = load_sheets()
    print(f"листов с себестоимостью: {len(sheets)}")

    rates: dict[str, list[float]] = {key: [] for key in OURS}
    rows = []
    for sheet in sheets:
        cost = sheet["cost"]
        components = {}
        for key in ("manufacturing", "install", "design", "other", "profit", "tech"):
            value = sheet.get(key)
            if value is not None:
                components[key] = value
                rates[key].append(value / cost)

        their_total = sheet.get("subtotal_row") or 0.0
        their_cash = sheet.get("cash")

        # Наш пересчёт: фиксированные расходы берём их, ставки — наши.
        base_without_install = cost * (
            1 + OURS["manufacturing"] + OURS["design"] + OURS["other"]
        )
        base_without_install += sheet.get("measure", 0.0)
        markup = (1 + OURS["profit"]) * (1 + OURS["tech"])
        share = OURS["install"] * markup
        installation = base_without_install * share / (1 - share)
        our_total = (base_without_install + installation) * markup
        our_total += sheet.get("delivery", 0.0)
        # «ИТОГО за наличку» у них = база + прибыль + техдир, без наценки.
        our_cash = our_total
        our_noncash = our_total * NONCASH

        rows.append({
            "file": sheet["file"],
            "sheet": sheet["sheet"],
            "cost": round(cost),
            "their_total": round(their_total),
            "their_cash": round(their_cash) if their_cash else None,
            "our_cash": round(our_cash),
            "our_noncash": round(our_noncash),
            "delta_cash": (
                round((our_cash - their_cash) / their_cash * 100, 1) if their_cash else None
            ),
        })

    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'коэффициент':16} {'их медиана':>11} {'наш':>7} {'разница':>9}")
    for key, values in rates.items():
        if not values:
            continue
        median = statistics.median(values)
        print(f"{key:16} {median:>11.3f} {OURS[key]:>7.2f} {median - OURS[key]:>+9.3f}")

    cash_deltas = [r["delta_cash"] for r in rows if r["delta_cash"] is not None]
    if cash_deltas:
        within5 = sum(1 for d in cash_deltas if abs(d) <= 5) / len(cash_deltas) * 100
        within10 = sum(1 for d in cash_deltas if abs(d) <= 10) / len(cash_deltas) * 100
        print(f"\nналичка: медиана {statistics.median(cash_deltas):+.1f} %, "
              f"±5 % — {within5:.0f} %, ±10 % — {within10:.0f} % (листов {len(cash_deltas)})")

        print(f"\n{'файл / лист':52} {'их наличка':>11} {'наш':>10} {'Δ%':>7}")
        for row in sorted(rows, key=lambda item: -abs(item["delta_cash"] or 0))[:12]:
            name = f"{Path(row['file']).stem[:26]} / {row['sheet'][:22]}"
            print(f"{name:52} {row['their_cash']:>11} {row['our_cash']:>10} {row['delta_cash']:>+6.1f}%")

    print(f"\nСохранено: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
