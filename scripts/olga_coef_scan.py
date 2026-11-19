"""Сбор фактических коэффициентов из расчётов Olga_project.

Ищет в xlsx блоки «СЕБЕСТОИМОСТЬ / ИЗГОТОВЛЕНИЕ / МОНТАЖ / ... / ПРИБЫЛЬ /
безнал» и снимает числа. Нужен, чтобы сверить наши 0.8/0.55/0.30/1.10
с тем, как реально считали заказы.

Запуск: .\\.venv\\Scripts\\python.exe scripts/olga_coef_scan.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path("Olga_project")
OUT = Path("docs/olga_coef_scan.json")

KEYS = {
    "cost": ("СЕБЕСТОИМОСТЬ",),
    "manufacturing": ("ИЗГОТОВЛЕНИЕ",),
    "install": ("МОНТАЖ",),
    "measure": ("ЗАМЕР",),
    "delivery": ("ДОСТАВКА",),
    "design": ("ПРОЕКТИРОВКА",),
    "other": ("ПРОЧИЕ РАСХОДЫ",),
    "total1": ("ИТОГО",),
    "profit": ("ПЛАНИРУЕМАЯ ПРИБЫЛЬ", "ПРИБЫЛЬ"),
    "tech": ("ТЕХНИЧЕСКИЙ ДИРЕКТОР",),
    "cash": ("ИТОГО за наличку", "ИТОГО ЗА НАЛИЧКУ"),
    "noncash": ("безнал", "БЕЗНАЛ"),
}
NUM = re.compile(r"-?\d[\d\s]*(?:[.,]\d+)?")


def _num(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = NUM.search(value.replace("\xa0", " "))
        if match:
            try:
                return float(match.group(0).replace(" ", "").replace(",", "."))
            except ValueError:
                return None
    return None


def scan_file(path: Path) -> list[dict]:
    rows = []
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception:  # noqa: BLE001
        return rows
    for ws in wb.worksheets:
        title = ws.title
        values = list(ws.iter_rows(values_only=True))
        for row_index, row in enumerate(values):
            cells = list(row)
            for col, cell in enumerate(cells):
                if not isinstance(cell, str):
                    continue
                text = cell.strip().upper().rstrip(":").strip()
                # Побеждает самая длинная подходящая метка: «ИТОГО за наличку»
                # раньше, чем просто «ИТОГО».
                matches = [
                    (len(v), key)
                    for key, variants in KEYS.items()
                    for v in variants
                    if text == v.upper()
                ]
                if not matches:
                    continue
                key = max(matches)[1]
                numbers = [_num(c) for c in cells[col + 1:col + 6]]
                numbers = [n for n in numbers if n is not None]
                if not numbers:
                    continue
                rows.append({
                    "file": str(path.relative_to(ROOT.parent)),
                    "sheet": title,
                    "row": row_index + 1,
                    "key": key,
                    "value": numbers[0],
                    "label": cell.strip()[:60],
                })
                break
    wb.close()
    return rows


def main() -> int:
    result = []
    for path in sorted(ROOT.rglob("*.xlsx")):
        found = scan_file(path)
        if found:
            result.append({"file": str(path.relative_to(ROOT.parent)), "rows": found})
            keys = {item["key"] for item in found}
            print(f"{path.relative_to(ROOT.parent)}: {sorted(keys)}")
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nфайлов с блоком расчёта: {len(result)} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
