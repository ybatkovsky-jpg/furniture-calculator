"""Высокий модуль должен считаться пеналом, а не нижней базой."""

from __future__ import annotations

from app.services.image_analyzer import GeminiImageAnalyzer, RecognizedModule


def module(kind: str, height: int) -> RecognizedModule:
    return RecognizedModule(type=kind, width=600, depth=560, height=height, quantity=1)


def test_tall_lower_becomes_penal() -> None:
    modules = [module("lower_base", 2500), module("lower_base", 820)]
    result = GeminiImageAnalyzer._reclassify_tall_modules(modules)
    assert [m.type for m in result] == ["penal", "lower_base"]


def test_upper_stays_upper() -> None:
    modules = [module("upper_base", 900)]
    result = GeminiImageAnalyzer._reclassify_tall_modules(modules)
    assert result[0].type == "upper_base"


def test_border_height_is_penal() -> None:
    modules = [module("lower_base", 1800)]
    result = GeminiImageAnalyzer._reclassify_tall_modules(modules)
    assert result[0].type == "penal"
