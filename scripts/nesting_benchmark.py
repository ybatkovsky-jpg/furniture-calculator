"""Бенчмарк MaxRects / Guillotine / Skyline на выгрузках ПРО100.

Запуск:
    .\\.venv\\Scripts\\python.exe scripts\\nesting_benchmark.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pro100_details_parse import parse_line

from app.domain.nesting_algorithms import ALGORITHMS, Rect, pack_multi

PROJECTS = ROOT / "tests" / "fixtures" / "projects"
SHEET = (2800, 2070)


def _parts(path: Path) -> list[Rect]:
    parts: list[Rect] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parsed = parse_line(line.lstrip("\ufeff"))
        if not parsed:
            continue
        material = str(parsed.get("material") or "")
        if "Покрыт" in material:
            continue
        width = int(parsed.get("width") or 0)
        height = int(parsed.get("height") or 0)
        thickness = float(parsed.get("thickness") or 0)
        quantity = int(parsed.get("quantity") or 1)
        if width < 100 or height < 100 or thickness < 10:
            continue
        for _ in range(quantity):
            parts.append(Rect(f"{path.parent.name}:{width}x{height}", width, height))
    return parts


def main() -> int:
    for path in sorted(PROJECTS.glob("*/details.txt")):
        parts = _parts(path)
        if not parts:
            continue
        print(f"\n{path.parent.name}: деталей {len(parts)}")
        for name, algorithm in ALGORITHMS.items():
            sheets, unplaced = pack_multi(algorithm, parts, *SHEET)
            placed = [part for sheet in sheets for part in sheet]
            used = sum(p.width_mm * p.height_mm for p in placed) / 1_000_000
            print(
                f"  {name:10} размещено {len(placed):4}  "
                f"не размещено {len(unplaced):3}  площадь {used:7.2f} м²  "
                f"листов {len(sheets):2}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
