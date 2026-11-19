"""Извлечение количеств из отчётов PRO100: materials.txt и elements.txt.

materials.txt: «путь материала <TAB> число <TAB> единица» (м², м, шт).
elements.txt:  «название элемента <TAB> количество».

Запуск: .\\.venv\\Scripts\\python.exe scripts/pro100_reports_extract.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path("docs/pro100_reports")
OUT = Path("docs/pro100_quantities.json")

LINE = re.compile(r"^(?P<name>.+?)\t\s*(?P<value>\d+(?:[.,]\d+)?)\s*\t?\s*(?P<unit>м²|м2|м|шт)?\s*$")


def parse_materials(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.lstrip("\ufeff").rstrip()
        if not line:
            continue
        match = LINE.match(line)
        if not match:
            continue
        unit = (match.group("unit") or "").strip()
        if unit not in ("м²", "м2", "м", "шт"):
            continue
        rows.append({
            "name": match.group("name").strip(),
            "value": float(match.group("value").replace(",", ".")),
            "unit": "м²" if unit == "м2" else unit,
        })
    return rows


def parse_elements(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.lstrip("\ufeff").rstrip()
        if "\t" not in line:
            continue
        name, _, qty = line.partition("\t")
        try:
            value = float(qty.replace(",", "."))
        except ValueError:
            continue
        rows.append({"name": name.strip(), "quantity": value})
    return rows


def main() -> int:
    summary = []
    for project_dir in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        materials_path = project_dir / "materials.txt"
        elements_path = project_dir / "elements.txt"
        materials = parse_materials(materials_path) if materials_path.exists() else []
        elements = parse_elements(elements_path) if elements_path.exists() else []

        total_area = sum(m["value"] for m in materials if m["unit"] == "м²")
        ldsp_area = sum(
            m["value"] for m in materials
            if m["unit"] == "м²" and "ЛДСП" in m["name"]
        )
        ldsp_sheets = sum(
            m["value"] for m in materials
            if m["unit"] == "шт" and "ЛДСП" in m["name"]
        )
        hinges = sum(
            e["quantity"] for e in elements
            if "петл" in e["name"].casefold()
        )
        drawers = sum(
            e["quantity"] for e in elements
            if "направляющ" in e["name"].casefold() or "tandem" in e["name"].casefold()
        )
        summary.append({
            "project": project_dir.name,
            "area_m2": round(total_area, 2),
            "ldsp_area_m2": round(ldsp_area, 2),
            "ldsp_sheets": round(ldsp_sheets),
            "hinges": round(hinges),
            "drawers": round(drawers),
            "materials": materials,
            "elements": elements,
        })

    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"проектов: {len(summary)}")
    print(f"{'проект':40} {'площадь м²':>10} {'петель':>7} {'ящиков':>7}")
    for row in sorted(summary, key=lambda item: -item["area_m2"])[:15]:
        print(f"{row['project'][:40]:40} {row['area_m2']:>10} "
              f"{row['hinges']:>7} {row['drawers']:>7}")
    print(f"\nСохранено: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
