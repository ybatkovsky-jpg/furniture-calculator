"""Стенд замера качества распознавания по эталонному альбому (P0).

Считает по каждой странице:
  - габарит модели против эталона;
  - покрытие по ширине = сумма(ширин модулей) / габарит;
  - двери и ящики против эталона;
  - флаги: покрытие < 95 %, расхождение габарита > 10 %, несовпадение дверей.

Запуск:
    .\\.venv\\Scripts\\python.exe scripts/eval_album.py            # все страницы
    .\\.venv\\Scripts\\python.exe scripts/eval_album.py --page 0002
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.image_analyzer import GeminiImageAnalyzer

ALBUM = Path("input_images")
GOLDEN = Path("tests/fixtures/album_golden.json")
OUT_JSON = Path("docs/eval_album.json")
OUT_MD = Path("docs/eval_album_2026-11-19.md")

COVERAGE_MIN = 0.95
COVERAGE_MAX = 1.10
WIDTH_TOLERANCE = 0.10


UPPER_TYPES = {"upper_base", "penal", "shelf_unit"}


def module_stats(modules) -> tuple[float, float, int, int]:
    """Ширина нижнего яруса, ширина верхнего яруса, двери, ящики.

    Нижний и верхний ряды стоят друг над другом в одном плане, поэтому
    складывать их нельзя — покрытие считаем по самому заполненному ярусу.
    """
    lower_width = 0.0
    upper_width = 0.0
    doors = 0
    drawers = 0
    for module in modules:
        quantity = getattr(module, "quantity", 1) or 1
        width = (getattr(module, "width", 0) or 0) * quantity
        if getattr(module, "type", "") in UPPER_TYPES:
            upper_width += width
        else:
            lower_width += width
        facades = getattr(module, "facades", None) or {}
        if isinstance(facades, dict):
            doors += int(facades.get("count") or 0) * quantity
        box = getattr(module, "drawers", None) or {}
        if isinstance(box, dict):
            drawers += int(box.get("count") or 0) * quantity
    return lower_width, upper_width, doors, drawers


async def evaluate(page: dict, analyzer: GeminiImageAnalyzer) -> dict:
    image = next(
        (ALBUM / p.name for p in ALBUM.glob(f"*page-{page['page']}.jpg")),
        None,
    )
    if image is None:
        return {"page": page["page"], "error": "файл не найден"}

    result = await analyzer.analyze_page(image, return_meta=True)
    modules, zone_type, _materials, confidence, meta = result
    lower_width, upper_width, doors, drawers = module_stats(modules)
    width_sum = max(lower_width, upper_width)
    total_width = int(meta.get("total_width_mm") or 0)
    coverage = round(width_sum / total_width, 3) if total_width else None

    flags = []
    if coverage is not None and coverage < COVERAGE_MIN:
        flags.append(f"покрытие {coverage:.0%} < 95% — состав неполный")
    if coverage is not None and coverage > COVERAGE_MAX:
        flags.append(f"покрытие {coverage:.0%} > 110% — состав не сходится с габаритом")
    expected_width = page.get("width_mm")
    width_delta = None
    if expected_width and total_width:
        width_delta = round((total_width - expected_width) / expected_width * 100, 1)
        if abs(width_delta) > WIDTH_TOLERANCE * 100:
            flags.append(f"габарит {total_width} против {expected_width} ({width_delta:+.1f}%)")
    expected_doors = page.get("doors")
    if expected_doors is not None and abs(doors - expected_doors) > 1:
        flags.append(f"дверей {doors}, ожидалось {expected_doors}")

    return {
        "page": page["page"],
        "zone": zone_type or page.get("zone"),
        "total_width_mm": total_width,
        "expected_width_mm": expected_width,
        "width_delta_pct": width_delta,
        "modules_width_sum_mm": int(width_sum),
        "lower_width_mm": int(lower_width),
        "upper_width_mm": int(upper_width),
        "coverage": coverage,
        "doors": doors,
        "expected_doors": expected_doors,
        "drawers": drawers,
        "modules": len(modules),
        "confidence": confidence,
        "scale_ok": bool(meta.get("scale_ok")),
        # Оценка по проверяемым фактам (P1), а не слово модели.
        "quality_score": meta.get("quality_score"),
        "quality_flags": list(meta.get("quality_flags") or []),
        "model_confidence": confidence,
        "image_flags": list(meta.get("image_flags") or []),
        "image_skew_deg": meta.get("image_skew_deg"),
        "flags": flags,
    }


def render_report(rows: list[dict]) -> str:
    measured = [r for r in rows if "error" not in r]
    covered = [r for r in measured if r["coverage"] is not None]
    good_width = [r for r in measured if r["width_delta_pct"] is not None
                  and abs(r["width_delta_pct"]) <= WIDTH_TOLERANCE * 100]
    doors_ok = [r for r in measured if r["expected_doors"] is not None
                and r["doors"] == r["expected_doors"]]
    doors_total = [r for r in measured if r["expected_doors"] is not None]
    flagged = [r for r in measured if r["flags"]]

    lines = [
        "# Замер распознавания по эталонному альбому",
        "",
        f"Дата: {datetime.now():%d.%m.%Y %H:%M}. Страниц: {len(measured)}.",
        "",
        "## Сводка",
        "",
        f"- покрытие посчитано: {len(covered)} страниц; "
        f"медиана {sorted(r['coverage'] for r in covered)[len(covered) // 2]:.0%}"
        if covered else "- покрытие не посчитано",
        f"- габарит в допуске ±10 %: {len(good_width)} из {len(measured)}",
        f"- двери совпали: {len(doors_ok)} из {len(doors_total)}",
        f"- страниц с флагами: {len(flagged)}",
        "",
        "## По страницам",
        "",
        "| Лист | Габарит | Эталон | Δ | Покрытие | Двери | Модули | Оценка | Флаги |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in measured:
        delta = f"{row['width_delta_pct']:+.1f}%" if row["width_delta_pct"] is not None else "—"
        coverage = f"{row['coverage']:.0%}" if row["coverage"] is not None else "—"
        doors = f"{row['doors']} / {row['expected_doors']}" if row["expected_doors"] is not None else str(row["doors"])
        flags = "; ".join(row["flags"]) or "—"
        score = f"{row['quality_score']:.2f}" if row.get("quality_score") is not None else "—"
        lines.append(
            f"| {row['page']} | {row['total_width_mm']} | {row['expected_width_mm'] or '—'} | "
            f"{delta} | {coverage} | {doors} | {row['modules']} | {score} | {flags} |"
        )
    lines.append("")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", help="только одна страница, например 0002")
    args = parser.parse_args()

    pages = json.loads(GOLDEN.read_text(encoding="utf-8"))["pages"]
    if args.page:
        pages = [p for p in pages if p["page"] == args.page]

    analyzer = GeminiImageAnalyzer()
    rows = []
    try:
        for page in pages:
            row = await evaluate(page, analyzer)
            rows.append(row)
            status = row.get("error") or "; ".join(row["flags"]) or "ок"
            print(f"{row['page']}: {status}", flush=True)
    finally:
        await analyzer.close()

    existing = {}
    if OUT_JSON.exists():
        existing = {r["page"]: r for r in json.loads(OUT_JSON.read_text(encoding="utf-8"))}
    existing.update({r["page"]: r for r in rows})
    ordered = [existing[key] for key in sorted(existing)]
    OUT_JSON.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_MD.write_text(render_report(ordered), encoding="utf-8")
    print(f"\nОтчёт: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
