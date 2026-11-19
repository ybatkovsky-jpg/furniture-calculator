"""Оценка качества состава по проверяемым фактам, а не по слову модели.

Модель ставит `confidence` почти всегда «high», поэтому доверять ей нельзя.
Здесь считаем то, что можно проверить арифметикой:

- покрытие по ширине: сумма ширин самого заполненного яруса / габарит;
- правдоподобие: двери на метр ширины;
- наличие модулей и дверей там, где они обязаны быть.

Результат — число 0..1 и список причин. Ниже 0,6 КП блокируется
(`calculation_service.QUALITY_SCORE_MIN`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Пенал стоит на полу и занимает ту же линию плана, что нижние модули,
# поэтому в покрытии он относится к нижнему ярусу.
UPPER_TYPES = {"upper_base", "shelf_unit"}
KITCHEN_ZONES = {"кухня", "kitchen"}
DOORS_PER_M_MIN = 1.5
DOORS_PER_M_MAX = 3.5
COVERAGE_MIN = 0.95
COVERAGE_MAX = 1.10


@dataclass
class CompositionQuality:
    """Проверяемая оценка состава листа."""

    score: float
    coverage: float | None
    coverage_sum: float | None
    lower_width_mm: int
    upper_width_mm: int
    doors: int
    drawers: int
    doors_per_m: float | None
    flags: list[str] = field(default_factory=list)

    @property
    def is_reliable(self) -> bool:
        return self.score >= 0.6


def _field(module: Any, name: str, default: Any = None) -> Any:
    if isinstance(module, dict):
        return module.get(name, default)
    return getattr(module, name, default)


def _count(module: Any, name: str) -> int:
    payload = _field(module, name) or {}
    if isinstance(payload, dict):
        return int(payload.get("count") or 0)
    return 0


def assess(
    modules: list[Any],
    total_width_mm: float | None,
    zone_type: str | None = None,
) -> CompositionQuality:
    """Оценить состав листа по арифметике, без доверия к `confidence`."""
    flags: list[str] = []
    score = 1.0

    lower = upper = 0.0
    doors = drawers = 0
    for module in modules:
        quantity = int(_field(module, "quantity", 1) or 1)
        width = float(_field(module, "width", 0) or 0) * quantity
        if str(_field(module, "type", "") or "") in UPPER_TYPES:
            upper += width
        else:
            lower += width
        doors += _count(module, "facades") * quantity
        drawers += _count(module, "drawers") * quantity

    total = float(total_width_mm or 0)
    coverage = None
    coverage_sum = None
    if total > 0:
        coverage = round(max(lower, upper) / total, 3)
        # Сумма всех модулей: на листах, где пеналы стоят рядом с нижним рядом,
        # «по ярусам» даёт ложный недобор, а сумма сходится с габаритом.
        coverage_sum = round((lower + upper) / total, 3)

    if total <= 0:
        score -= 0.5
        flags.append("габарит не прочитан — проверить размерную линию")
    elif coverage is not None and coverage > COVERAGE_MAX:
        score -= 0.35
        flags.append(f"покрытие {coverage:.0%} > 110% — модули из разных проекций")
    elif (
        coverage is not None and coverage < COVERAGE_MIN
        and (coverage_sum is None or coverage_sum < COVERAGE_MIN)
    ):
        score -= 0.35
        flags.append(f"покрытие {coverage:.0%} < 95% — состав неполный")

    doors_per_m = round(doors / (total / 1000), 2) if total > 0 else None
    if doors_per_m is not None and doors_per_m > DOORS_PER_M_MAX:
        score -= 0.2
        flags.append(
            f"дверей {doors_per_m} на метр — больше обычного ({DOORS_PER_M_MAX})"
        )

    if not modules:
        score -= 0.5
        flags.append("модули не найдены")

    zone = (zone_type or "").strip().lower()
    if zone in KITCHEN_ZONES and doors == 0 and drawers == 0:
        score -= 0.4
        flags.append("кухня без дверей и ящиков — вероятно потеряны фасады")

    return CompositionQuality(
        score=round(max(0.0, min(1.0, score)), 2),
        coverage=coverage,
        coverage_sum=coverage_sum,
        lower_width_mm=int(lower),
        upper_width_mm=int(upper),
        doors=doors,
        drawers=drawers,
        doors_per_m=doors_per_m,
        flags=flags,
    )
