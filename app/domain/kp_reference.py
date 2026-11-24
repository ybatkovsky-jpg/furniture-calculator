"""Эталон 22 КП: пары «наличка/безнал» и коридоры цен.

Это не замена расчёта: в КП нет состава модулей. Но он фиксирует
коммерческую политику (×1,10 / ×1,13) и цены по типам мебели.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "kp_reference_2026-11-24.json"
)


def load_pairs(path: Path = FIXTURE) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [pair for doc in data.get("documents", []) for pair in doc.get("pairs", [])]


def markup_summary(pairs: list[dict]) -> dict:
    """Сколько пар на 10 %, на 13 % и какая медиана."""
    markups = [float(pair.get("markup") or 0) for pair in pairs]
    at_10 = sum(1 for markup in markups if abs(markup - 0.10) <= 0.005)
    at_13 = sum(1 for markup in markups if abs(markup - 0.13) <= 0.01)
    return {
        "pairs": len(pairs),
        "at_10": at_10,
        "at_13": at_13,
        "median": round(median(markups), 4) if markups else 0.0,
        "min": round(min(markups), 4) if markups else 0.0,
        "max": round(max(markups), 4) if markups else 0.0,
    }


def _percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * share)))
    return ordered[index]


def price_corridor(pairs: list[dict], item_type: str) -> dict:
    """Коридор наличных цен по типу мебели (по вхождению слова)."""
    values = sorted(
        float(pair["cash"])
        for pair in pairs
        if item_type.lower() in str(pair.get("item", "")).lower()
    )
    if not values:
        return {"type": item_type, "count": 0}
    return {
        "type": item_type,
        "count": len(values),
        "min": values[0],
        "p10": _percentile(values, 0.10),
        "median": values[len(values) // 2],
        "p90": _percentile(values, 0.90),
        "max": values[-1],
    }


def check_price_corridor(
    cash: float,
    item_type: str,
    *,
    pairs: list[dict] | None = None,
) -> dict:
    """Попадает ли цена КП в 10–90 % коридора 22 КП."""
    corridor = price_corridor(pairs if pairs is not None else load_pairs(), item_type)
    if corridor["count"] < 3:
        return {
            "ok": None,
            "reason": "для этого типа мало эталонных КП",
            "corridor": corridor,
        }
    if corridor["p10"] <= cash <= corridor["p90"]:
        return {"ok": True, "reason": "в коридоре 10–90 %", "corridor": corridor}
    return {
        "ok": False,
        "reason": f"цена {cash:.0f} ₽ вне коридора "
                  f"{corridor['p10']:.0f}–{corridor['p90']:.0f} ₽",
        "corridor": corridor,
    }
