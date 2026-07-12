"""
Стандартные размеры и правила расчёта по типам мебели.

Добавление нового типа мебели = +1 запись в FURNITURE_DEFAULTS.
Код quantity_calc не нужно править — он читает правила отсюда.
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class FurnitureRules:
    """Правила расчёта для одного типа мебели (зоны)."""
    # Стандартные размеры по типу модуля (мм)
    default_depth_lower: int = 560
    default_depth_upper: int = 320
    default_height_lower: int = 820
    default_height_upper: int = 720
    default_height_penal: int = 2500
    default_height_corner: int = 820

    # Глубины для других типов
    default_depth_wardrobe: int = 600
    default_depth_tall: int = 500
    default_depth_shelf: int = 400
    default_depth_vanity: int = 500

    # Система дверей: "hinged" (распашные, нужны петли) или "sliding" (купе)
    door_system: str = "hinged"

    # Фасады по умолчанию есть?
    has_facades: bool = True

    # Авто-аксессуары (какие позиции автоматически добавлять)
    auto_countertop: bool = False
    auto_led: bool = False
    auto_gola: bool = False
    auto_drawers: bool = False
    auto_drying_rack: bool = False
    auto_bottle_holder: bool = False
    auto_cutlery_tray: bool = False
    auto_rods: bool = False        # штанги (для гардеробных)
    auto_shelves: bool = False     # доп. полки (для открытых стеллажей)
    auto_mirror: bool = False      # зеркало (для прихожих/шкафов)

    # Семейство помещения (для группировки эвристик)
    family: str = "default"


# ═══════════════════════════════════════════════════════════════════
# ТАБЛИЦА СТАНДАРТОВ ПО ТИПАМ ПОМЕЩЕНИЙ
# ═══════════════════════════════════════════════════════════════════

FURNITURE_DEFAULTS: Dict[str, FurnitureRules] = {
    # ── Кухня ──
    "Кухня": FurnitureRules(
        default_depth_lower=560, default_depth_upper=320,
        default_height_lower=820, default_height_upper=720,
        default_height_penal=2500,
        door_system="hinged",
        auto_countertop=True, auto_led=True, auto_gola=True,
        auto_drawers=True, auto_drying_rack=True,
        auto_bottle_holder=True, auto_cutlery_tray=True,
        family="kitchen",
    ),
    "kitchen": FurnitureRules(
        default_depth_lower=560, default_depth_upper=320,
        default_height_lower=820, default_height_upper=720,
        default_height_penal=2500,
        door_system="hinged",
        auto_countertop=True, auto_led=True, auto_gola=True,
        auto_drawers=True, auto_drying_rack=True,
        auto_bottle_holder=True, auto_cutlery_tray=True,
        family="kitchen",
    ),

    # ── Гардеробная ──
    "Гардеробная": FurnitureRules(
        default_depth_lower=600, default_depth_upper=400,
        default_height_lower=2500, default_height_upper=2500,
        default_height_penal=2500,
        door_system="sliding",
        auto_rods=True, auto_shelves=True,
        family="wardrobe",
    ),
    "wardrobe": FurnitureRules(
        default_depth_lower=600, default_depth_upper=400,
        default_height_lower=2500, default_height_upper=2500,
        default_height_penal=2500,
        door_system="sliding",
        auto_rods=True, auto_shelves=True,
        family="wardrobe",
    ),

    # ── Прихожая ──
    "Прихожая": FurnitureRules(
        default_depth_lower=400, default_depth_upper=350,
        default_height_lower=2400, default_height_upper=800,
        door_system="hinged",
        auto_rods=True, auto_shelves=True, auto_mirror=True,
        family="hallway",
    ),

    # ── Шкаф-купе (в спальню, гостиную) ──
    "Спальня": FurnitureRules(
        default_depth_lower=600, default_depth_upper=600,
        default_height_lower=2500, default_height_upper=2500,
        door_system="sliding",
        auto_rods=True, auto_shelves=True,
        family="wardrobe",
    ),
    "Гостиная": FurnitureRules(
        default_depth_lower=500, default_depth_upper=350,
        default_height_lower=2400, default_height_upper=800,
        door_system="hinged",
        auto_shelves=True,
        family="living",
    ),

    # ── Кабинет ──
    "Кабинет": FurnitureRules(
        default_depth_lower=500, default_depth_upper=350,
        default_height_lower=750, default_height_upper=700,
        door_system="hinged",
        auto_shelves=True,
        family="office",
    ),

    # ── Ванная ──
    "Ванная": FurnitureRules(
        default_depth_lower=500, default_depth_upper=300,
        default_height_lower=820, default_height_upper=700,
        auto_countertop=True,  # столешница под раковину
        family="bathroom",
    ),

    # ── Детская ──
    "Детская": FurnitureRules(
        default_depth_lower=500, default_depth_upper=350,
        default_height_lower=750, default_height_upper=700,
        auto_shelves=True,
        family="living",
    ),

    # ── Балкон ──
    "Балкон": FurnitureRules(
        default_depth_lower=400, default_depth_upper=300,
        default_height_lower=2400, default_height_upper=600,
        auto_shelves=True,
        family="open",
    ),

    # ── Постирочная ──
    "Постирочная": FurnitureRules(
        default_depth_lower=600, default_depth_upper=350,
        default_height_lower=820, default_height_upper=720,
        auto_countertop=True,
        family="utility",
    ),

    # ── Столовая ──
    "Столовая": FurnitureRules(
        default_depth_lower=500, default_depth_upper=350,
        default_height_lower=820, default_height_upper=720,
        family="living",
    ),
}


# ═══════════════════════════════════════════════════════════════════
# ТИПЫ МОДУЛЕЙ (полная таксономия)
# ═══════════════════════════════════════════════════════════════════

MODULE_TYPES = {
    # Кухонные
    "lower_base":   "Напольный модуль (на полу, H=700-900, D=500-600)",
    "upper_base":   "Навесной модуль (на стене, H=600-1000, D=280-350)",
    "penal":        "Высокий шкаф от пола (H=1800-2800, с краю)",
    "corner":       "Угловой модуль (квадратный, W=D)",

    # Универсальные
    "tall_cabinet": "Высокий шкаф (H=2000-2500, не пенал, может быть в ряду)",
    "wardrobe":     "Шкаф-купе (H=2400-2800, D=600, раздвижные двери)",
    "shelf_unit":   "Стеллаж / открытые полки (без фасадов)",
    "vanity":       "Тумба под раковину (H=820, D=500)",
    "drawer_unit":  "Модуль только с ящиками (без распашных дверей)",
    "open_unit":    "Открытый модуль (без фасадов, только полки)",
    "wall_panel":   "Декоративная панель (40-80мм ширины, без расчёта)",
    "countertop":   "Столешница / стойка (не модуль, справочно)",
}


def get_rules(zone_type: Optional[str]) -> FurnitureRules:
    """Получить правила расчёта для типа помещения."""
    if not zone_type:
        return FurnitureRules()
    return FURNITURE_DEFAULTS.get(zone_type, FurnitureRules())


def get_family(zone_type: Optional[str]) -> str:
    """Получить семейство помещения (kitchen/wardrobe/living/open/...)."""
    return get_rules(zone_type).family
