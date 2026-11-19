"""Панели PRO100 → модули (корпуса).

Эвристика: боковина — панель, у которой ширина равна глубине корпуса
(400–700 мм), а высота — высоте корпуса (300–2800). Пара таких панелей
(quantity >= 2) = один корпус. Дно/крыша — панели с высотой, равной
глубине корпуса. Полки — панели той же толщины с высотой, равной глубине.

Запуск: .\\.venv\\Scripts\\python.exe scripts/pro100_panels_to_modules.py
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / ".."))
sys.path.insert(0, "scripts")

from pro100_details_parse import parse_line

ROOT = Path("docs/pro100_reports")
OUT = Path("docs/pro100_modules_summary.json")

DEPTH_MIN, DEPTH_MAX = 350, 750
HEIGHT_MIN, HEIGHT_MAX = 300, 2800
THICKNESS = (16.0, 18.0, 25.0, 32.0)


def load_panels(details: Path) -> list[dict]:
    rows = []
    for line in details.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = parse_line(line.lstrip("\ufeff"))
        if not parsed:
            continue
        if not (2 <= parsed["thickness"] <= 60):
            continue
        if parsed["material"].startswith("Покрытия"):
            continue
        if parsed["width"] > 3200 or parsed["height"] > 3200:
            continue
        rows.append(parsed)
    return rows


def build_modules(panels: list[dict]) -> dict:
    sides: dict[tuple, float] = defaultdict(float)
    for panel in panels:
        depth = panel["width"]
        height = panel["height"]
        if DEPTH_MIN <= depth <= DEPTH_MAX and HEIGHT_MIN <= height <= HEIGHT_MAX:
            key = (depth, height, panel["thickness"])
            sides[key] += panel["quantity"]

    carcasses = []
    for (depth, height, thickness), count in sides.items():
        number = int(count // 2)
        if number >= 1:
            carcasses.append({
                "depth": depth,
                "height": height,
                "thickness": thickness,
                "count": number,
            })

    depths = {round(c["depth"] / 10) * 10 for c in carcasses}
    shelves = 0
    tops = 0
    for panel in panels:
        is_top = (
            round(panel["height"] / 10) * 10 in depths
            and panel["width"] >= 250
            and panel["height"] <= DEPTH_MAX
        )
        if is_top:
            tops += panel["quantity"]
    shelf_like = [
        p for p in panels
        if any(abs(p["height"] - d) <= 15 for d in depths) and p["width"] < 1500
    ]
    shelves = sum(p["quantity"] for p in shelf_like)

    return {
        "carcasses": len(carcasses),
        "modules": sum(c["count"] for c in carcasses),
        "depths": sorted({c["depth"] for c in carcasses}),
        "heights": sorted({c["height"] for c in carcasses}),
        "shelves": shelves,
        "tops": tops,
        "details": carcasses,
    }


def main() -> int:
    summary = []
    for details in sorted(ROOT.rglob("details.txt")):
        panels = load_panels(details)
        if not panels:
            continue
        modules = build_modules(panels)
        if modules["modules"] == 0:
            continue
        modules["project"] = details.parent.name
        modules["panels"] = len(panels)
        summary.append(modules)

    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"проектов с корпусами: {len(summary)}")
    print(f"{'проект':40} {'корпусов':>9} {'глубины':>16} {'высоты':>18} {'полок':>6}")
    for row in sorted(summary, key=lambda item: -item["modules"])[:15]:
        depths = ",".join(str(int(d)) for d in row["depths"][:3])
        heights = ",".join(str(int(h)) for h in row["heights"][:3])
        print(f"{row['project'][:40]:40} {row['modules']:>9} {depths:>16} {heights:>18} {row['shelves']:>6}")

    all_depths = [d for row in summary for d in row["depths"]]
    all_heights = [h for row in summary for h in row["heights"]]
    print("\nчастая глубина:", Counter(int(d) for d in all_depths).most_common(5))
    print("частая высота:", Counter(int(h) for h in all_heights).most_common(5))
    print(f"медиана корпусов на проект: {statistics.median(r['modules'] for r in summary):.0f}")
    print(f"\nСохранено: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
