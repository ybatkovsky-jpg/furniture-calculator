"""Валидация распознанных значений: источник, статус, блокирующие вопросы."""

from __future__ import annotations

from app.domain.recognition_validation import validate_modules
from app.services.image_analyzer import RecognizedModule


def _module(**kwargs) -> RecognizedModule:
    base = {
        "type": "lower_base",
        "width": 600,
        "depth": 560,
        "height": 820,
        "quantity": 1,
        "facades": {"count": 2},
        "drawers": {"count": 1},
    }
    base.update(kwargs)
    return RecognizedModule(**base)


def test_every_value_has_source_and_status() -> None:
    validation = validate_modules(
        [_module()], page_index=2, confidence="high", meta={"total_width_mm": 600}
    )

    assert validation.can_calculate is True
    assert validation.blocking == []
    assert len(validation.values) == 5
    width = validation.values[0]
    assert width.name == "module[1].width"
    assert width.value == 600
    assert width.source_page == 3
    assert width.source_type == "vision"
    assert width.verification_status == "recognized"
    assert width.confidence == 0.9


def test_missing_width_blocks_and_asks_question() -> None:
    validation = validate_modules([_module(width=0)], confidence="high")

    assert validation.can_calculate is False
    assert validation.blocking
    assert "ширин" in validation.questions[0].lower()
    width = validation.values[0]
    assert width.verification_status == "missing"


def test_low_confidence_marks_review_but_does_not_block() -> None:
    validation = validate_modules([_module()], confidence="low")

    assert validation.can_calculate is True
    assert validation.warnings
    assert any(
        value.verification_status == "needs_review" for value in validation.values
    )


def test_module_wider_than_total_is_blocking() -> None:
    validation = validate_modules(
        [_module(width=1200)], confidence="high", meta={"total_width_mm": 600}
    )

    assert validation.can_calculate is False
    assert any("шире габарита" in item for item in validation.blocking)


def test_possible_coverage_is_only_a_warning() -> None:
    validation = validate_modules(
        [_module(width=600)], confidence="high", meta={"total_width_mm": 3000}
    )

    assert validation.can_calculate is True
    assert validation.warnings


def test_duplicate_modules_are_warned_but_not_blocked() -> None:
    validation = validate_modules(
        [_module(), _module()], confidence="high", meta={"total_width_mm": 1200}
    )

    assert validation.can_calculate is True
    assert any("дублирования" in item for item in validation.warnings)


def test_unrealistic_height_blocks() -> None:
    validation = validate_modules([_module(height=5000)], confidence="high")

    assert validation.can_calculate is False
    assert any("нереальной" in item for item in validation.blocking)


def test_too_many_drawers_for_height_is_warned() -> None:
    validation = validate_modules(
        [_module(height=300, drawers={"count": 4})], confidence="high"
    )

    assert validation.can_calculate is True
    assert any("меньше 100 мм" in item for item in validation.warnings)


def test_overlapping_bboxes_are_warned() -> None:
    validation = validate_modules(
        [
            _module(bbox={"x": 0, "y": 0, "w": 300, "h": 300}),
            _module(bbox={"x": 50, "y": 50, "w": 300, "h": 300}),
        ],
        confidence="high",
    )

    assert validation.can_calculate is True
    assert any("перекрываются" in item for item in validation.warnings)


