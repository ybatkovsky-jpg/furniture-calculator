"""Подготовка изображения: тёмный/смазанный скан и наклон (P5)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from app.services.image_prep import assess, deskew, estimate_skew


def _save(image: Image.Image, path: Path) -> Path:
    image.save(path)
    return path


def test_dark_scan_is_rejected(tmp_path: Path) -> None:
    image = Image.new("L", (600, 400), color=20)
    quality = assess(_save(image, tmp_path / "dark.jpg"))
    assert quality.ok is False
    assert any("тёмный" in flag for flag in quality.flags)


def test_blurred_scan_is_flagged(tmp_path: Path) -> None:
    array = np.tile(np.linspace(40, 200, 600, dtype=np.uint8), (400, 1))
    image = Image.fromarray(array, mode="L")
    quality = assess(_save(image, tmp_path / "blur.jpg"))
    assert any("смазан" in flag for flag in quality.flags)


def test_sharp_page_is_ok(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    array = rng.integers(60, 200, size=(400, 600), dtype=np.uint8)
    image = Image.fromarray(array, mode="L")
    quality = assess(_save(image, tmp_path / "sharp.jpg"))
    assert quality.ok is True
    assert quality.flags == [] or all("наклон" in flag for flag in quality.flags)


def test_skew_is_estimated(tmp_path: Path) -> None:
    image = Image.new("L", (800, 600), color=255)
    draw = ImageDraw.Draw(image)
    for y in range(80, 560, 40):
        draw.line([(60, y), (740, y)], fill=0, width=3)
    rotated = image.rotate(2.0, resample=Image.Resampling.BILINEAR, fillcolor=255)
    path = _save(rotated, tmp_path / "skewed.jpg")
    arr = np.asarray(Image.open(path).convert("L"), dtype=np.float32)
    angle = estimate_skew(arr)
    assert 1.0 <= abs(angle) <= 3.0


def test_deskew_returns_image(tmp_path: Path) -> None:
    image = Image.new("L", (400, 300), color=255)
    rotated = image.rotate(1.5, resample=Image.Resampling.BILINEAR, fillcolor=255)
    result = deskew(rotated, 1.5)
    assert result.size[0] >= 400 and result.size[1] >= 300
