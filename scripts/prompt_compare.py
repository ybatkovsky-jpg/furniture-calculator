"""Сравнение версий промпта по сохранённому кэшу (без вызовов модели).

В `data/recognition_cache` лежат ответы разных версий промпта на одни и те же
листы. Считаем по каждой версии: сколько страниц совпало с эталоном по дверям
и медианное отклонение габарита. Это бесплатная проверка «какой промпт лучше».

Запуск:
    .\\.venv\\Scripts\\python.exe scripts/prompt_compare.py
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path

ALBUM = Path("input_images")
CACHE = Path("data/recognition_cache")
GOLDEN = Path("tests/fixtures/album_golden.json")
OUT = Path("docs/prompt_compare.json")


def page_key(page: str) -> str | None:
    image = next(ALBUM.glob(f"*page-{page}.jpg"), None)
    if image is None:
        return None
    return hashlib.sha256(image.read_bytes()).hexdigest()[:16]


def doors_of(data: dict) -> int:
    total = 0
    for module in data.get("modules") or []:
        quantity = module.get("quantity") or 1
        facades = module.get("facades") or {}
        total += int(facades.get("count") or 0) * quantity
    return total


def main() -> int:
    pages = json.loads(GOLDEN.read_text(encoding="utf-8"))["pages"]
    versions: dict[str, dict[str, dict]] = defaultdict(dict)

    for page in pages:
        key = page_key(page["page"])
        if key is None:
            continue
        for path in CACHE.glob(f"{key}_*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            data = payload.get("data") or {}
            version = payload.get("prompt_version") or path.name.split("_")[1][:8]
            versions[version][page["page"]] = {
                "doors": doors_of(data),
                "width": int(data.get("total_width_mm") or 0),
            }

    rows = []
    for version, pages_data in versions.items():
        matched = 0
        compared = 0
        width_deltas = []
        for page in pages:
            entry = pages_data.get(page["page"])
            if not entry:
                continue
            expected_doors = page.get("doors")
            if expected_doors is not None:
                compared += 1
                matched += entry["doors"] == expected_doors
            expected_width = page.get("width_mm")
            if expected_width and entry["width"]:
                width_deltas.append(abs(entry["width"] - expected_width) / expected_width * 100)
        rows.append({
            "prompt_version": version,
            "pages": len(pages_data),
            "doors_compared": compared,
            "doors_matched": matched,
            "median_width_delta_pct": round(statistics.median(width_deltas), 1) if width_deltas else None,
        })

    rows.sort(key=lambda row: (-row["doors_matched"], row["median_width_delta_pct"] or 999))
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'версия':10} {'страниц':>8} {'двери':>12} {'габарит, медиана':>18}")
    for row in rows:
        print(f"{row['prompt_version']:10} {row['pages']:>8} "
              f"{row['doors_matched']:>5} из {row['doors_compared']:<5} "
              f"{row['median_width_delta_pct']!s:>18}")
    print(f"\nсохранено: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
