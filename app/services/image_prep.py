"""Подготовка изображения до модели: яркость, резкость, наклон (P5).

Модель не читает тёмный, смазанный или перекошенный скан. Раньше такие листы
уходили в распознавание «как есть» и давали правдоподобный, но неверный состав.
Здесь считаем три проверяемых признака и умеем выравнивать наклон.

Реализовано на numpy+Pillow: OpenCV в проекте нет и тянуть его незачем.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

ANALYSIS_SIZE = 1024
DARK_MEAN = 45.0
BLUR_VARIANCE_MIN = 60.0
SKEW_WARN_DEG = 0.7
SKEW_RANGE = 3.0
SKEW_STEP = 0.25


@dataclass
class ImageQuality:
    """Проверяемые признаки скана."""

    ok: bool
    brightness: float
    blur_variance: float
    skew_deg: float
    flags: list[str] = field(default_factory=list)


def _gray_array(image: Image.Image, size: int = ANALYSIS_SIZE) -> np.ndarray:
    gray = image.convert("L")
    if max(gray.size) > size:
        ratio = size / max(gray.size)
        gray = gray.resize((int(gray.width * ratio), int(gray.height * ratio)))
    return np.asarray(gray, dtype=np.float32)


def _laplacian_variance(arr: np.ndarray) -> float:
    """Резкость: дисперсия лапласиана (чем меньше — тем смазаннее)."""
    kernel = np.array([[0.0, 1.0, 0.0], [1.0, -4.0, 1.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    h, w = arr.shape
    if h < 3 or w < 3:
        return 0.0
    result = np.zeros((h - 2, w - 2), dtype=np.float32)
    for dy in range(3):
        for dx in range(3):
            weight = kernel[dy, dx]
            if weight:
                result += weight * arr[dy:dy + h - 2, dx:dx + w - 2]
    return float(result.var())


def _projection_score(arr: np.ndarray, angle: float) -> float:
    """Чем ровнее строки текста/линий, тем выше разброс суммы по строкам."""
    if angle == 0:
        rotated = arr
    else:
        image = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        rotated_img = image.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255)
        rotated = np.asarray(rotated_img, dtype=np.float32)
    binary = (rotated < rotated.mean()).astype(np.float32)
    rows = binary.sum(axis=1)
    return float(rows.var())


def estimate_skew(arr: np.ndarray) -> float:
    """Оценить наклон в градусах по максимуму разброса проекции строк."""
    best_angle = 0.0
    best_score = -1.0
    angle = -SKEW_RANGE
    while angle <= SKEW_RANGE + 1e-9:
        score = _projection_score(arr, angle)
        if score > best_score:
            best_score = score
            best_angle = angle
        angle += SKEW_STEP
    return round(best_angle, 2)


def assess(path: str | Path) -> ImageQuality:
    """Проверить скан до отправки в модель."""
    image = Image.open(path)
    arr = _gray_array(image)
    brightness = float(arr.mean())
    blur = _laplacian_variance(arr)

    flags: list[str] = []
    if brightness < DARK_MEAN:
        flags.append(f"скан тёмный (яркость {brightness:.0f} < {DARK_MEAN:.0f})")
    if blur < BLUR_VARIANCE_MIN:
        flags.append(f"скан смазан (резкость {blur:.0f} < {BLUR_VARIANCE_MIN:.0f})")

    skew = 0.0
    if not flags:
        skew = estimate_skew(arr)
        if abs(skew) > SKEW_WARN_DEG:
            flags.append(f"наклон {skew:+.1f}° — нужен deskew")

    return ImageQuality(
        ok=not any("тёмный" in flag or "смазан" in flag for flag in flags),
        brightness=round(brightness, 1),
        blur_variance=round(blur, 1),
        skew_deg=skew,
        flags=flags,
    )


def deskew(image: Image.Image, angle: float) -> Image.Image:
    """Повернуть изображение на найденный угол (белый фон)."""
    if abs(angle) < 0.01:
        return image
    # Для «L» и «1» режимов заливка задаётся числом, для цветных — кортежем.
    fill = 255 if image.mode in ("L", "1", "I;16") else (255, 255, 255)
    return image.rotate(
        angle,
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor=fill,
    )
