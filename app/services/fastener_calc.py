"""
Расчёт крепежа и установочной фурнитуры.

Версия 1.0 — по промпту v4.0 (2026-07-11):
- Конфирматы 7×50: точная формула по количеству деталей
- Регулируемые опоры: 4/6 шт. на нижнюю базу
- Настенные подвесы: 2 шт. на верхнюю базу
- Цоколь ПВХ: метраж / 4м
- Саморезы 3,5×16: для стыков модулей
- Шканты и эксцентрики (если используется система минификс)
"""

import math
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class FastenerQuantities:
    """Рассчитанные количества крепежа и установочной фурнитуры."""
    # Конфирматы
    confirmat_7x50: int = 0          # конфирматы 7×50 (основной крепёж корпуса)
    
    # Саморезы
    self_tapping_35x16: int = 0      # саморезы 3,5×16 (стыки модулей)
    
    # Опоры и подвесы
    adjustable_feet: int = 0         # регулируемые опоры
    wall_mounts: int = 0             # настенные подвесы (для верхних баз)
    
    # Цоколь
    plinth_strips: int = 0           # планки цоколя ПВХ (4м)
    plinth_corners: int = 0          # уголки для цоколя
    
    # Минификсы (опционально)
    minifix_housings: int = 0        # корпуса эксцентриков
    minifix_bolts: int = 0           # штоки эксцентриков
    dowels_8x30: int = 0             # шканты 8×30
    
    # Стоимость
    total_cost: float = 0.0


def calculate_fasteners(
    lower_module_count: int = 0,
    upper_module_count: int = 0,
    total_modules: int = 0,
    shelves_count: int = 0,
    partitions_count: int = 0,
    lower_modules_width_m: float = 0.0,
    *,
    use_minifix: bool = False,       # использовать минификсы вместо конфирматов
) -> FastenerQuantities:
    """
    Рассчитать полный комплект крепежа и установочной фурнитуры.

    Args:
        lower_module_count: количество нижних баз
        upper_module_count: количество верхних баз
        total_modules: общее количество модулей (для конфирматов)
        shelves_count: общее количество полок
        partitions_count: количество вертикальных перегородок
        lower_modules_width_m: суммарная ширина нижних баз (метры)
        use_minifix: использовать систему минификс (эксцентрики + шканты)

    Returns:
        FastenerQuantities с итоговыми количествами
    """
    q = FastenerQuantities()

    # Всегда считаем конфирматы (или минификсы)
    lids_count = total_modules       # ≈ каждая база имеет крышку
    bottoms_count = total_modules    # и дно

    # ── Конфирматы 7×50 ──
    # Формула: (полки×4) + (крышки×6) + (днища×6) + (перегородки×4) + 10% запас
    confirmat_base = (
        (shelves_count * 4) +
        (lids_count * 6) +
        (bottoms_count * 6) +
        (partitions_count * 4)
    )
    q.confirmat_7x50 = math.ceil(confirmat_base * 1.10)
    
    # ── Саморезы 3,5×16 (стыки модулей) ──
    # По 4 шт. на каждый стык между соседними модулями
    total_for_joints = max(0, total_modules - 1)  # количество стыков
    q.self_tapping_35x16 = total_for_joints * 4
    
    # ── Регулируемые опоры ──
    # 4 шт. на нижнюю базу, 6 шт. на широкую (>900 мм)
    # Здесь упрощённо: 4 на каждую нижнюю базу
    q.adjustable_feet = lower_module_count * 4
    
    # ── Настенные подвесы ──
    # 2 шт. на каждую верхнюю базу
    q.wall_mounts = upper_module_count * 2
    
    # ── Цоколь ПВХ ──
    if lower_modules_width_m > 0:
        q.plinth_strips = math.ceil(lower_modules_width_m * 1.10 / 4)
        q.plinth_corners = 2  # минимум 2 уголка (левый + правый)
    
    # ── Минификсы (опционально) ──
    if use_minifix:
        # Каждое соединение полка-боковина: 2 эксцентрика + 2 шканта
        joints = shelves_count + lids_count + bottoms_count + partitions_count
        q.minifix_housings = joints * 2
        q.minifix_bolts = joints * 2
        q.dowels_8x30 = joints * 2
        # Конфирматы не нужны при минификсах
        q.confirmat_7x50 = 0
    
    return q


def calculate_fasteners_from_modules(
    modules: List,
    room_name: str = "",
) -> FastenerQuantities:
    """
    Рассчитать крепёж на основе списка RecognizedModule.
    Удобная обёртка для использования в конвейере.

    Args:
        modules: список RecognizedModule
        room_name: название помещения

    Returns:
        FastenerQuantities
    """
    lower_count = 0
    upper_count = 0
    total = 0
    total_shelves = 0
    total_partitions = 0
    lower_width_m = 0.0

    for m in modules:
        qty = max(m.quantity, 1)
        total += qty

        if m.type == "lower_base":
            lower_count += qty
            lower_width_m += (m.width / 1000) * qty
        elif m.type == "upper_base":
            upper_count += qty

        # Полки
        shelf_n = max(m.shelves, 1 if m.type in ("lower_base", "upper_base") else 0)
        total_shelves += shelf_n * qty

        # Перегородки
        if m.facades and m.facades.get("count", 1) > 1:
            total_partitions += (m.facades.get("count", 1) - 1) * qty

    return calculate_fasteners(
        lower_module_count=lower_count,
        upper_module_count=upper_count,
        total_modules=total,
        shelves_count=total_shelves,
        partitions_count=total_partitions,
        lower_modules_width_m=lower_width_m,
    )


# ═══════════════════════════════════════════════════════════════════
# СПРАВОЧНЫЕ ЦЕНЫ (для ориентира)
# ═══════════════════════════════════════════════════════════════════

FASTENER_PRICES = {
    "confirmat_7x50": 2.0,           # ₽/шт
    "self_tapping_35x16": 1.0,       # ₽/шт
    "adjustable_foot": 50.0,         # ₽/шт
    "wall_mount": 120.0,             # ₽/шт
    "plinth_strip_4m": 1500.0,       # ₽/шт (Rehau)
    "plinth_corner": 250.0,          # ₽/комплект
    "minifix_housing": 5.0,          # ₽/шт
    "minifix_bolt": 4.0,             # ₽/шт
    "dowel_8x30": 2.0,               # ₽/шт
}
