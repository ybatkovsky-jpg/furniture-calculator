"""Выбор одной проекции на многоракурсном листе.

На листе часто несколько видов одной кухни (спереди, сбоку, сверху). Модель
перечисляет двери со всех видов, и их ширины складываются в сумму больше
габарита — на `0002` покрытие выходило 116 %.

Правило: кластеризуем фасады по горизонтали (разрыв больше 12 % ширины листа
делит кластеры) и берём тот вид, чья сумма ширин ближе к габариту с чертежа.
На однопроекционном листе кластер один — ничего не меняется.
"""

from __future__ import annotations

from typing import Any

GAP_PCT = 12.0
IDEAL_MIN = 0.85
IDEAL_MAX = 1.15


def _field(facade: Any, name: str, default: float = 0.0) -> float:
    if isinstance(facade, dict):
        return float(facade.get(name, default) or default)
    return float(getattr(facade, name, default) or default)


def split_clusters(facades: list[Any], gap_pct: float = GAP_PCT) -> list[list[Any]]:
    """Разбить фасады на группы по горизонтальным разрывам."""
    if not facades:
        return []

    ordered = sorted(facades, key=lambda f: _field(f, "bbox_x_pct"))
    clusters: list[list[Any]] = [[ordered[0]]]
    for facade in ordered[1:]:
        previous = clusters[-1][-1]
        previous_right = _field(previous, "bbox_x_pct") + _field(previous, "bbox_w_pct")
        gap = _field(facade, "bbox_x_pct") - previous_right
        if gap > gap_pct:
            clusters.append([facade])
        else:
            clusters[-1].append(facade)
    return clusters


def select_main_projection(
    facades: list[Any],
    total_width_mm: float | None,
    gap_pct: float = GAP_PCT,
) -> tuple[list[Any], dict]:
    """Оставить фасады одной проекции — той, что лучше сходится с габаритом."""
    info: dict = {"clusters": 0, "chosen": 0, "widths_mm": [], "reason": "одна проекция"}
    clusters = split_clusters(facades, gap_pct)
    info["clusters"] = len(clusters)
    if len(clusters) <= 1:
        return facades, info

    widths = [sum(_field(f, "width_mm") for f in cluster) for cluster in clusters]
    info["widths_mm"] = [round(width) for width in widths]

    total = float(total_width_mm or 0)
    if total > 0:
        def score(index: int) -> tuple[int, float]:
            ratio = widths[index] / total
            ideal = 0 if IDEAL_MIN <= ratio <= IDEAL_MAX else 1
            return (ideal, abs(ratio - 1.0))

        best = min(range(len(clusters)), key=score)
        info["reason"] = (
            f"выбрана проекция с шириной {widths[best]:.0f} мм "
            f"при габарите {total:.0f} мм"
        )
    else:
        best = max(range(len(clusters)), key=lambda index: widths[index])
        info["reason"] = "габарит неизвестен — взята самая широкая проекция"

    info["chosen"] = best
    return clusters[best], info
