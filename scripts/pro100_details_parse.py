"""Разбор деталировки PRO100 (details.txt) в сводку по проектам.

Формат строки: [название] <TAB> W <TAB> H <TAB> толщина <TAB> количество <TAB> материал.
Строки с материалом «Покрытия\\...» и толщиной 200 — стены/обои, их отбрасываем.

Запуск: .\\.venv\\Scripts\\python.exe scripts/pro100_details_parse.py
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.services.importing.pro100_importer import parse_details_line

ROOT = Path("docs/pro100_reports")
OUT = Path("docs/pro100_details_summary.json")
SHEET_AREA = 5.796  # 2800×2070
RESERVE = 1.3


def parse_line(line: str) -> dict | None:
    """Совместимая обёртка над единым парсером импорта."""
    row = parse_details_line(line)
    if row is None:
        return None
    return {
        "name": row["name"],
        "width": row["width_mm"],
        "height": row["height_mm"],
        "thickness": row["thickness_mm"],
        "quantity": row["quantity"],
        "material": row["material"],
    }


def main() -> int:
    summary = []
    for details in sorted(ROOT.rglob("details.txt")):
        project = details.parent.name
        rows = []
        for line in details.read_text(encoding="utf-8", errors="replace").splitlines():
            parsed = parse_line(line.lstrip("\ufeff"))
            if parsed:
                rows.append(parsed)

        furniture = [
            r for r in rows
            if 2 <= r["thickness"] <= 60
            and r["width"] <= 3200 and r["height"] <= 3200
            and not r["material"].startswith("Покрытия")
        ]
        area = sum(r["width"] * r["height"] * r["quantity"] for r in furniture) / 1_000_000
        by_thickness = Counter()
        for row in furniture:
            by_thickness[int(row["thickness"])] += (
                row["width"] * row["height"] * row["quantity"] / 1_000_000
            )
        sheets = math.ceil(area * RESERVE / SHEET_AREA) if area else 0
        materials = Counter(
            row["material"].split("\\")[0] for row in furniture if row["material"]
        )
        summary.append({
            "project": project,
            "panels": len(furniture),
            "area_m2": round(area, 3),
            "by_thickness": {k: round(v, 3) for k, v in sorted(by_thickness.items())},
            "sheets_est": sheets,
            "materials": dict(materials.most_common(5)),
            "details_bytes": details.stat().st_size,
        })

    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    total = len(summary)
    with_area = [row for row in summary if row["area_m2"] > 0.1]
    print(f"проектов: {total}, с деталями: {len(with_area)}")
    print(f"{'проект':40} {'панелей':>8} {'площадь м²':>11} {'листов':>7}")
    for row in sorted(with_area, key=lambda item: -item["area_m2"])[:15]:
        print(f"{row['project'][:40]:40} {row['panels']:>8} {row['area_m2']:>11} {row['sheets_est']:>7}")
    print(f"\nСохранено: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
