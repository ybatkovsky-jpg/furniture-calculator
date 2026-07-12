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
        logger.warning("Недостаточно данных для масштаба — используем стандартные размеры")
        scale = 600 / 20  # условно: 600мм = 20%
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


def distribute_heights_proportionally(
    facades: List[ScaledFacade],
    zone: str,
    total_height_mm: int,
    image_height_px: Optional[int] = None,
    image_width_px: Optional[int] = None,
) -> List[ScaledFacade]:
    """
    Если для зоны известна общая высота — распределить пропорционально bbox.
    Пока не используется, оставлено для будущих улучшений.
    """
    return facades


def calculate_scaled_facades_px(
    facades_data: List[Dict],
    total_width_mm: float,
    ref_bbox: List[float],
    image_width: int = 1536,
    image_height: int = 1086,
    lower_height_mm: Optional[int] = None,
    upper_height_mm: Optional[int] = None,
    penal_height_mm: Optional[int] = None,
) -> List[ScaledFacade]:
    """
    Вычислить размеры фасадов по ТОЧНЫМ пиксельным координатам bbox.

    Args:
        facades_data: [{"zone":"lower","bbox":[x1,y1,x2,y2]}, ...]
        total_width_mm: общий габарит нижних баз в мм
        ref_bbox: [x1,y1,x2,y2] — пиксельные координаты размерной линии габарита
        image_width, image_height: размер изображения в пикселях
        lower/upper/penal_height_mm: высоты по зонам (если None — стандарт)

    Returns:
        Список ScaledFacade с width_mm и height_mm
    """
    # 1. Масштаб по эталонной размерной линии
    if ref_bbox and len(ref_bbox) == 4 and total_width_mm > 0:
        ref_width_px = abs(ref_bbox[2] - ref_bbox[0])
        if ref_width_px > 0:
            scale = total_width_mm / ref_width_px  # мм на пиксель
        else:
            scale = 3000 / 1500  # fallback
    else:
        # Без ref_bbox — считаем по всем нижним фасадам
        lower_items = [f for f in facades_data if f.get("zone") == "lower"]
        if lower_items:
            total_px = sum(abs(f["bbox"][2] - f["bbox"][0]) for f in lower_items if len(f.get("bbox", [])) == 4)
            scale = total_width_mm / total_px if total_px > 0 else 2.0
        else:
            scale = 2.0

    logger.info(f"Масштаб (px): {total_width_mm}мм / эталон = {scale:.3f} мм/px")

    # 2. Высоты по зонам
    heights = {
        "lower": lower_height_mm or DEFAULT_HEIGHTS["lower"],
        "upper": upper_height_mm or DEFAULT_HEIGHTS["upper"],
        "penal": penal_height_mm or DEFAULT_HEIGHTS["penal"],
    }

    # 3. Вычисляем размеры каждого фасада
    result = []
    for f in facades_data:
        bbox = f.get("bbox", [0, 0, 100, 100])
        if len(bbox) != 4:
            continue

        zone = f.get("zone", "lower")
        px_w = abs(bbox[2] - bbox[0])

        width_mm = round(px_w * scale)
        height_mm = heights.get(zone, 716)

        # Валидация
        if width_mm < 200:
            logger.warning(f"Фасад {zone} px_w={px_w} → {width_mm}мм (узкий) → корректируем до 250мм")
            width_mm = 250
        elif width_mm > 1200:
            logger.warning(f"Фасад {zone} px_w={px_w} → {width_mm}мм (широкий) → корректируем до 1000мм")
            width_mm = 1000

        result.append(ScaledFacade(
            zone=zone,
            width_mm=width_mm,
            height_mm=height_mm,
            bbox_x_pct=bbox[0] / image_width * 100,
            bbox_w_pct=px_w / image_width * 100,
        ))

    # 4. Проверка
    lower_result = [r for r in result if r.zone == "lower"]
    if lower_result:
        sum_w = sum(r.width_mm for r in lower_result)
        diff = abs(sum_w - total_width_mm)
        if diff > 150:
            logger.warning(f"Расхождение: сумма нижних {sum_w}мм ≠ габарит {total_width_mm}мм (Δ={diff}мм)")

    return result
