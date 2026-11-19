"""P3: два прохода по одному листу и объединение составов.

Проход 1 — `analyze_page` (промпт с фасадами и габаритом, есть кэш).
Проход 2 — `analyze_drawing` (другой промпт, простой модульный анализ).

Сравниваем двери и модули, объединяем по подписи модуля
(тип, ширина и высота с шагом 50 мм) — берём максимум по каждой подписи.

Запуск:
    .\\.venv\\Scripts\\python.exe scripts/two_pass_pilot.py --page 0002
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.image_analyzer import GeminiImageAnalyzer
from app.services.two_pass import PassMetrics, consensus

ALBUM = Path("input_images")
OUT = Path("docs/two_pass_pilot.json")


def signature(module) -> tuple:
    width = int(round((getattr(module, "width", 0) or 0) / 50) * 50)
    height = int(round((getattr(module, "height", 0) or 0) / 50) * 50)
    kind = str(getattr(module, "type", "") or "")
    return (kind, width, height)


def doors_of(module) -> int:
    data = getattr(module, "facades", None) or {}
    return int(data.get("count") or 0) if isinstance(data, dict) else 0


def union(primary, secondary) -> dict:
    merged: dict[tuple, dict] = {}
    for module in list(primary) + list(secondary):
        key = signature(module)
        quantity = int(getattr(module, "quantity", 1) or 1)
        doors = doors_of(module)
        entry = merged.setdefault(key, {"quantity": 0, "doors_per_module": 0})
        entry["quantity"] = max(entry["quantity"], quantity)
        entry["doors_per_module"] = max(entry["doors_per_module"], doors)
    doors = sum(item["quantity"] * item["doors_per_module"] for item in merged.values())
    return {"modules": len(merged), "doors": doors}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", default="0002")
    parser.add_argument("--force-reask", action="store_true",
                        help="выполнить переспрос даже при согласии проходов")
    args = parser.parse_args()

    image = next(ALBUM.glob(f"*page-{args.page}.jpg"))
    analyzer = GeminiImageAnalyzer()
    try:
        first = await analyzer.analyze_page(image)
        first_modules, _zone, _materials, first_conf = first[:4]
        second_result = await analyzer.analyze_drawing(image)
    finally:
        await analyzer.close()

    first_doors = sum(doors_of(m) * int(getattr(m, "quantity", 1) or 1) for m in first_modules)
    second_doors = sum(
        doors_of(m) * int(getattr(m, "quantity", 1) or 1) for m in second_result.modules
    )
    merged = union(first_modules, second_result.modules)

    decision = consensus(
        PassMetrics(doors=first_doors, modules=len(first_modules), confidence=first_conf),
        PassMetrics(doors=second_doors, modules=len(second_result.modules),
                    confidence=second_result.confidence),
    )

    reask = None
    if (args.force_reask or not decision.reliable) and decision.agreement is not None:
        hint = (
            f"При первом проходе найдено {first_doors} дверей, при втором {second_doors}. "
            "Перечисли каждую дверь отдельной записью с координатами (bbox), "
            "не объединяй соседние двери в одну и не пропускай пеналы."
        )
        analyzer2 = GeminiImageAnalyzer()
        try:
            third = await analyzer2.analyze_page(image, hint=hint)
            third_modules = third[0]
            reask = {
                "doors": sum(
                    doors_of(m) * int(getattr(m, "quantity", 1) or 1) for m in third_modules
                ),
                "modules": len(third_modules),
                "hint": hint,
            }
        finally:
            await analyzer2.close()

    report = {
        "page": args.page,
        "pass1": {"modules": len(first_modules), "doors": first_doors, "confidence": first_conf},
        "pass2": {
            "modules": len(second_result.modules),
            "doors": second_doors,
            "confidence": second_result.confidence,
        },
        "union_by_signature": merged,
        "reask": reask,
        "decision": {
            "doors": decision.doors,
            "modules": decision.modules,
            "agreement": decision.agreement,
            "reliable": decision.reliable,
            "flags": decision.flags,
        },
        "agreement": (
            round(
                2 * min(first_doors, second_doors)
                / (first_doors + second_doors),
                2,
            )
            if first_doors + second_doors
            else None
        ),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
