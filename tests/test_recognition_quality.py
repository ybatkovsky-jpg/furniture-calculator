"""Проверяемая оценка состава: покрытие, двери на метр, габарит."""

from __future__ import annotations

from app.services.recognition_quality import assess


def module(width: int, quantity: int = 1, kind: str = "lower_base",
           doors: int = 1, drawers: int = 0) -> dict:
    return {
        "type": kind,
        "width": width,
        "quantity": quantity,
        "facades": {"count": doors},
        "drawers": {"count": drawers} if drawers else None,
    }


def test_good_kitchen_scores_high() -> None:
    modules = [module(600, 4), module(600, 4, kind="upper_base")]
    quality = assess(modules, 2400, "Кухня")
    assert quality.coverage == 1.0
    assert quality.doors == 8
    assert quality.flags == []
    assert quality.score >= 0.9
    assert quality.is_reliable


def test_incomplete_composition_is_flagged() -> None:
    modules = [module(600, 2), module(600, 2, kind="upper_base")]
    quality = assess(modules, 4800, "Кухня")
    assert quality.coverage == 0.25
    assert any("неполный" in flag for flag in quality.flags)
    assert quality.score < 0.7


def test_over_coverage_is_flagged() -> None:
    modules = [module(900, 6)]
    quality = assess(modules, 3000, "Кухня")
    assert quality.coverage == 1.8
    assert any("не сходится" in flag for flag in quality.flags)


def test_missing_total_width_is_flagged() -> None:
    quality = assess([module(600, 2)], 0, "Кухня")
    assert quality.coverage is None
    assert any("габарит не прочитан" in flag for flag in quality.flags)
    assert quality.score <= 0.5


def test_kitchen_without_doors_is_flagged() -> None:
    modules = [module(600, 4, doors=0)]
    quality = assess(modules, 2400, "Кухня")
    assert any("без дверей" in flag for flag in quality.flags)


def test_too_many_doors_per_metre_is_flagged() -> None:
    modules = [module(500, 2, doors=5)]
    quality = assess(modules, 1000, "Кухня")
    assert quality.doors_per_m is not None
    assert any("на метр" in flag for flag in quality.flags)


def test_empty_page_scores_low() -> None:
    quality = assess([], 2400, "Кухня")
    assert quality.score <= 0.5
    assert any("не найдены" in flag for flag in quality.flags)
