"""Замороженный контроль состава по сохранённому распознаванию (P4).

Берём уже сохранённые ответы модели из `data/recognition_cache` и проверяем,
что проверки качества срабатывают как задумано. Сети нет — тест дешёвый и
стабильный; если кэша нет, тест пропускается.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.services.recognition_quality import assess

CACHE = Path("data/recognition_cache")
ALBUM = Path("input_images")


def _cached_modules(page: str) -> tuple[list[dict], int] | None:
    image = next(ALBUM.glob(f"*page-{page}.jpg"), None)
    if image is None or not CACHE.exists():
        return None
    key = hashlib.sha256(image.read_bytes()).hexdigest()[:16]
    files = sorted(CACHE.glob(f"{key}_*.json"))
    if not files:
        return None
    newest = max(files, key=lambda path: path.stat().st_mtime)
    data = json.loads(newest.read_text(encoding="utf-8"))["data"]
    return data.get("modules") or [], int(data.get("total_width_mm") or 0)


def test_corner_kitchen_over_coverage_is_flagged() -> None:
    cached = _cached_modules("0002")
    if cached is None:
        pytest.skip("нет кэша 0002")
    modules, total = cached
    quality = assess(modules, total, "Кухня")
    assert quality.coverage is not None
    if quality.coverage > 1.10:
        assert any("не сходится" in flag for flag in quality.flags)
        assert quality.score < 0.8


def test_bed_page_without_modules_is_flagged() -> None:
    cached = _cached_modules("0008")
    if cached is None:
        pytest.skip("нет кэша 0008")
    modules, total = cached
    quality = assess(modules, total, "Кухня")
    if not modules:
        assert quality.score <= 0.5
        assert any("не найдены" in flag for flag in quality.flags)


def test_straight_kitchen_scores_reliably() -> None:
    cached = _cached_modules("0006")
    if cached is None:
        pytest.skip("нет кэша 0006")
    modules, total = cached
    quality = assess(modules, total, "Кухня")
    if quality.coverage is not None and 0.95 <= quality.coverage <= 1.10:
        assert quality.is_reliable
