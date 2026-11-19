"""Пилот нарезки листа (P2): 4 квадранта вместо целого листа.

Режем страницу на 2×2 с перекрытием, распознаём каждый фрагмент отдельно и
показываем, что нашлось в каждом. Цель — проверить, перечисляет ли модель
больше состава на фрагментах, чем на целом листе.

Запуск:
    .\\.venv\\Scripts\\python.exe scripts/crop_pilot.py --page 0002
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image

from app.services.image_analyzer import GeminiImageAnalyzer

ALBUM = Path("input_images")
WORK = Path("temp/crop_pilot")
OVERLAP = float(__import__("os").getenv("CROP_OVERLAP", "0"))


def make_crops(image_path: Path, cols: int = 2, rows: int = 2) -> list[dict]:
    """Нарезать лист на cols×rows фрагментов с перекрытием 10 %."""
    WORK.mkdir(parents=True, exist_ok=True)
    image = Image.open(image_path)
    width, height = image.size
    step_x, step_y = width // cols, height // rows
    pad_x, pad_y = int(step_x * OVERLAP), int(step_y * OVERLAP)

    crops = []
    for row in range(rows):
        for col in range(cols):
            left = max(0, col * step_x - pad_x)
            top = max(0, row * step_y - pad_y)
            right = min(width, (col + 1) * step_x + pad_x)
            bottom = min(height, (row + 1) * step_y + pad_y)
            path = WORK / f"{image_path.stem}_{row}{col}.jpg"
            image.crop((left, top, right, bottom)).save(path, quality=92)
            crops.append({
                "path": path,
                "rect": (left, top, right, bottom),
                "name": f"r{row}c{col}",
            })
    return crops


def summarize(modules) -> dict:
    doors = 0
    drawers = 0
    for module in modules:
        quantity = getattr(module, "quantity", 1) or 1
        facades = getattr(module, "facades", None) or {}
        boxes = getattr(module, "drawers", None) or {}
        doors += int(facades.get("count") or 0) * quantity
        drawers += int(boxes.get("count") or 0) * quantity
    return {"modules": len(modules), "doors": doors, "drawers": drawers}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", default="0002")
    args = parser.parse_args()

    image_path = next(ALBUM.glob(f"*page-{args.page}.jpg"))
    crops = make_crops(image_path)
    analyzer = GeminiImageAnalyzer()

    report = []
    try:
        for crop in crops:
            result = await analyzer.analyze_drawing(crop["path"])
            stats = summarize(result.modules)
            stats.update({
                "crop": crop["name"],
                "zone": result.zone_type,
                "modules_list": [
                    {
                        "type": getattr(m, "type", ""),
                        "width": getattr(m, "width", 0),
                        "height": getattr(m, "height", 0),
                        "quantity": getattr(m, "quantity", 1),
                        "doors": (getattr(m, "facades", None) or {}).get("count", 0),
                    }
                    for m in result.modules
                ],
            })
            report.append(stats)
            print(f"{crop['name']}: модулей {stats['modules']}, дверей {stats['doors']}", flush=True)
    finally:
        await analyzer.close()

    total_doors = sum(r["doors"] for r in report)
    total_modules = sum(r["modules"] for r in report)
    print(f"\nвсего по фрагментам: модулей {total_modules}, дверей {total_doors} (с перекрытием)")
    Path("docs/crop_pilot.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("отчёт: docs/crop_pilot.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
