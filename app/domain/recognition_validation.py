"""Проверка распознанных значений и происхождение данных (этап 2).

Значение без источника не считается достоверным: каждое поле модуля получает
`source_page`, `source_type`, `confidence` и `verification_status`. Проверка
не меняет расчёт, а формирует вопросы менеджеру и блокирующие замечания.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CONFIDENCE_SCORE = {"high": 0.9, "medium": 0.65, "low": 0.35}


@dataclass
class RecognizedValue:
    """Одно проверяемое значение."""

    name: str
    value: float
    unit: str = "mm"
    source_page: int | None = None
    source_type: str = "vision"
    confidence: float = 0.0
    verification_status: str = "recognized"
    bbox: dict[str, int] | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PageValidation:
    """Результат проверки одной страницы."""

    page_index: int
    values: list[RecognizedValue] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def can_calculate(self) -> bool:
        return not self.blocking

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "values": [value.as_dict() for value in self.values],
            "questions": list(self.questions),
            "blocking": list(self.blocking),
            "warnings": list(self.warnings),
            "can_calculate": self.can_calculate,
        }


def validate_modules(
    modules: list[Any],
    *,
    page_index: int = 0,
    confidence: str = "medium",
    meta: dict[str, Any] | None = None,
    source_type: str = "vision",
) -> PageValidation:
    """Собрать значения с источником и проверить арифметику страницы."""
    result = PageValidation(page_index=page_index)
    score = CONFIDENCE_SCORE.get(str(confidence or "").lower(), 0.5)
    meta = meta or {}

    total_width = _number(meta.get("total_width_mm"))
    module_widths: list[float] = []
    seen_modules: dict[tuple, int] = {}

    for index, module in enumerate(modules or [], start=1):
        kind = str(getattr(module, "type", "") or "модуль")
        bbox = getattr(module, "bbox", None)
        for field_name, label, question_label in (
            ("width", "ширина", "ширину"),
            ("depth", "глубина", "глубину"),
            ("height", "высота", "высоту"),
        ):
            raw = _number(getattr(module, field_name, None))
            status = "recognized"
            note = ""
            if raw is None or raw <= 0:
                status = "missing"
                note = f"Модуль #{index} ({kind}): не найдена {label}"
                result.blocking.append(note)
                result.questions.append(
                    f"Укажите {question_label} модуля #{index} ({kind})"
                )
            elif score < 0.5:
                status = "needs_review"
                note = f"Низкая уверенность по {label} модуля #{index}"
                result.warnings.append(note)
            result.values.append(
                RecognizedValue(
                    name=f"module[{index}].{field_name}",
                    value=raw if raw is not None else 0,
                    source_page=page_index + 1,
                    source_type=source_type,
                    confidence=round(score, 2),
                    verification_status=status,
                    bbox=bbox if isinstance(bbox, dict) else None,
                    note=note,
                )
            )
            if field_name == "width" and raw and raw > 0:
                module_widths.append(raw)

        facades = getattr(module, "facades", None) or {}
        doors = _number(facades.get("count")) if isinstance(facades, dict) else 0
        drawers_raw = getattr(module, "drawers", None) or {}
        drawers = (
            _number(drawers_raw.get("count"))
            if isinstance(drawers_raw, dict)
            else 0
        )
        result.values.append(
            RecognizedValue(
                name=f"module[{index}].doors",
                value=doors or 0,
                unit="шт",
                source_page=page_index + 1,
                source_type=source_type,
                confidence=round(score, 2),
                verification_status="recognized",
                bbox=bbox if isinstance(bbox, dict) else None,
            )
        )
        result.values.append(
            RecognizedValue(
                name=f"module[{index}].drawers",
                value=drawers or 0,
                unit="шт",
                source_page=page_index + 1,
                source_type=source_type,
                confidence=round(score, 2),
                verification_status="recognized",
                bbox=bbox if isinstance(bbox, dict) else None,
            )
        )

        module_key = (
            kind,
            _number(getattr(module, "width", 0)),
            _number(getattr(module, "depth", 0)),
            _number(getattr(module, "height", 0)),
            doors,
            drawers,
        )
        if module_key in seen_modules:
            result.warnings.append(
                f"Модули #{seen_modules[module_key]} и #{index} совпадают — "
                "проверьте, нет ли дублирования"
            )
        else:
            seen_modules[module_key] = index

        width_value = _number(getattr(module, "width", 0)) or 0
        if doors and width_value and width_value / doors < 250:
            result.warnings.append(
                f"Модуль #{index}: на дверь приходится меньше 250 мм ширины"
            )
        height_value = _number(getattr(module, "height", 0)) or 0
        if drawers and height_value and height_value / drawers < 100:
            result.warnings.append(
                f"Модуль #{index}: на ящик приходится меньше 100 мм высоты"
            )
        if height_value > 3500:
            result.blocking.append(
                f"Модуль #{index}: высота {height_value:.0f} мм выглядит нереальной"
            )
            result.questions.append(f"Проверьте высоту модуля #{index}")

    if total_width and module_widths:
        # Верхние модули могут стоять над нижними: сравниваем с максимумом
        # покрытия, а не с суммой всех модулей без разбора ярусов.
        covered = max(module_widths)
        if covered > total_width * 1.1:
            message = (
                f"Модуль шире габарита: {covered:.0f} мм против {total_width:.0f} мм"
            )
            result.blocking.append(message)
            result.questions.append("Проверьте габарит и ширину самого широкого модуля")
        elif sum(module_widths) < total_width * 0.5:
            message = (
                f"Сумма ширин модулей {sum(module_widths):.0f} мм меньше "
                f"половины габарита {total_width:.0f} мм"
            )
            result.warnings.append(message)

    _check_bbox_overlaps(modules, result)

    if not modules:
        result.blocking.append("Модули не найдены")
        result.questions.append("Проверьте лист или загрузите другой чертёж")

    return result


def _check_bbox_overlaps(modules: list[Any], result: PageValidation) -> None:
    """Предупредить о сильном пересечении bbox: возможны дубли/проекции."""
    boxes: list[tuple[int, dict[str, float]]] = []
    for index, module in enumerate(modules or [], start=1):
        bbox = getattr(module, "bbox", None)
        if not isinstance(bbox, dict):
            continue
        try:
            box = {
                "x": float(bbox.get("x", 0)),
                "y": float(bbox.get("y", 0)),
                "w": float(bbox.get("w", 0)),
                "h": float(bbox.get("h", 0)),
            }
        except (TypeError, ValueError):
            continue
        if box["w"] > 0 and box["h"] > 0:
            boxes.append((index, box))

    for left in range(len(boxes)):
        for right in range(left + 1, len(boxes)):
            first_index, first = boxes[left]
            second_index, second = boxes[right]
            width = min(first["x"] + first["w"], second["x"] + second["w"]) - max(
                first["x"], second["x"]
            )
            height = min(first["y"] + first["h"], second["y"] + second["h"]) - max(
                first["y"], second["y"]
            )
            if width <= 0 or height <= 0:
                continue
            intersection = width * height
            smaller = min(first["w"] * first["h"], second["w"] * second["h"])
            if smaller and intersection / smaller >= 0.5:
                result.warnings.append(
                    f"Модули #{first_index} и #{second_index} перекрываются "
                    "на изображении — проверьте, не дубли или несколько проекций"
                )


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
