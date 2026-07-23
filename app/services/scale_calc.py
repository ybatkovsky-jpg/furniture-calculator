"""
Калькулятор размеров фасадов по bbox-процентам и эталонному габариту.

Модель даёт:
- total_width_mm: общий габарит нижних баз с чертежа (например, 3000мм)
- Для каждого фасада: bbox_x_pct, bbox_w_pct (проценты от ширины изображения)

Python считает:
- scale = total_width_mm / сумма(bbox_w_pct нижних фасадов)
- width_mm каждого фасада = bbox_w_pct × scale

Для высот:
- Модель читает высоту с чертежа ИЛИ
- Используется стандарт зоны: lower=716, upper=596, penal=2500
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ScaledFacade:
    """Фасад с вычисленными размерами в мм."""
    zone: str
    width_mm: int
    height_mm: int
    bbox_x_pct: float
    bbox_w_pct: float
    is_corner: bool = False   # признак углового фасада (от модели, пробрасывается из JSON)


# Стандартные высоты по зонам (если модель не прочитала с чертежа)
DEFAULT_HEIGHTS = {
    "lower": 716,
    "upper": 596,
    "penal": 2500,
}


def calculate_scaled_facades(
    facades_data: List[Dict],
    total_width_mm: float,
    lower_height_mm: Optional[int] = None,
    upper_height_mm: Optional[int] = None,
    penal_height_mm: Optional[int] = None,
) -> List[ScaledFacade]:
    """
    Вычислить реальные размеры фасадов по bbox-процентам.

    Args:
        facades_data: список фасадов от модели с полями zone, bbox_x_pct, bbox_w_pct
        total_width_mm: общий габарит нижних баз (мм) с чертежа
        lower_height_mm: высота нижних баз (если прочитана) или None
        upper_height_mm: высота верхних баз или None
        penal_height_mm: высота пеналов или None

    Returns:
        Список ScaledFacade с width_mm и height_mm
    """
    # 1. Вычисляем масштаб по нижним базам
    lower_facades = [f for f in facades_data if f.get("zone") == "lower"]
    if not lower_facades:
        # Если нет нижних — берём все фасады
        lower_facades = facades_data

    total_pct = sum(f.get("bbox_w_pct", 0) for f in lower_facades)
    if total_pct <= 0 or total_width_mm <= 0:
        logger.warning("Недостаточно данных для масштаба — масштабный анализ невозможен")
        return []
    else:
        scale = total_width_mm / total_pct  # мм на 1% ширины изображения
    logger.info(f"Масштаб: {total_width_mm}мм / {total_pct:.1f}% = {scale:.1f} мм/%")

    # 2. Высоты по зонам
    heights = {
        "lower": lower_height_mm or DEFAULT_HEIGHTS["lower"],
        "upper": upper_height_mm or DEFAULT_HEIGHTS["upper"],
        "penal": penal_height_mm or DEFAULT_HEIGHTS["penal"],
    }

    # 3. Вычисляем размеры каждого фасада
    result = []
    for f in facades_data:
        zone = f.get("zone", "lower")
        w_pct = f.get("bbox_w_pct", 20)
        x_pct = f.get("bbox_x_pct", 0)

        width_mm = round(w_pct * scale)

        # Валидация: ширина в разумных пределах
        if width_mm < 200:
            logger.warning(f"Фасад {zone} слишком узкий ({width_mm}мм) → корректируем до 250мм")
            width_mm = 250
        elif width_mm > 1200:
            logger.warning(f"Фасад {zone} слишком широкий ({width_mm}мм) → корректируем до 1000мм")
            width_mm = 1000

        height_mm = heights.get(zone, 716)

        result.append(ScaledFacade(
            zone=zone,
            width_mm=width_mm,
            height_mm=height_mm,
            bbox_x_pct=x_pct,
            bbox_w_pct=w_pct,
            is_corner=bool(f.get("is_corner", False)),
        ))

    # 4. Проверка: сумма ширин нижних фасадов ≈ total_width_mm
    lower_result = [r for r in result if r.zone == "lower"]
    if lower_result:
        sum_widths = sum(r.width_mm for r in lower_result)
        diff = abs(sum_widths - total_width_mm)
        if diff > 200:
            logger.warning(
                f"Расхождение: сумма ширин нижних фасадов {sum_widths}мм "
                f"≠ габарит {total_width_mm}мм (разница {diff}мм)"
            )

    return result


