"""Бот: вопросы валидации перед расчётом (этап 2)."""

from __future__ import annotations

from app.bot.handlers.image import format_validation_questions
from app.services.image_analyzer import RecognitionResult, RecognizedModule


def test_bot_shows_questions_for_missing_size() -> None:
    result = RecognitionResult(
        modules=[
            RecognizedModule(
                type="lower_base",
                width=0,
                depth=560,
                height=820,
                facades={"count": 1},
            )
        ],
        confidence="high",
    )

    text = format_validation_questions(result)

    assert "ширин" in text.lower()
    assert "заблокирован" in text.lower()


def test_bot_has_no_questions_for_valid_module() -> None:
    result = RecognitionResult(
        modules=[
            RecognizedModule(
                type="lower_base",
                width=600,
                depth=560,
                height=820,
                facades={"count": 1},
            )
        ],
        confidence="high",
    )

    assert format_validation_questions(result) == ""
