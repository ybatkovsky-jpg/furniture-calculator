"""
Расчёт кромки по деталям.

Пресеты кромления (ЧАСТЬ 9.3):
- Стандартный кухонный: видимые 0.8мм, скрытые без
- Улучшенный: видимые 2мм, скрытые 0.4мм
- Премиум: видимые 2мм, скрытые 0.8мм

Для каждой детали определяем видимые/скрытые торцы и считаем метраж.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class EdgePreset:
    """Пресет кромления."""
    name: str
    visible_edge: Optional[str]  # толщина для видимых торцов ("0.8", "2", etc)
    hidden_edge: Optional[str]   # толщина для скрытых торцов ("0.4", "0.8", None)
    description: str


# Пресеты кромления
EDGE_PRESETS = {
    "kitchen_standard": EdgePreset(
        name="Стандартный кухонный",
        visible_edge="0.8",
        hidden_edge=None,
        description="Лицевые: 0.8мм, скрытые: без"
    ),
    "improved": EdgePreset(
        name="Улучшенный",
        visible_edge="2",
        hidden_edge="0.4",
        description="Лицевые: 2мм, скрытые: 0.4мм"
    ),
    "premium": EdgePreset(
        name="Премиум",
        visible_edge="2",
        hidden_edge="0.8",
        description="Лицевые: 2мм, скрытые: 0.8мм"
    ),
}


@dataclass
class EdgeCalculation:
    """Результат расчёта кромки."""
    total_cost: float = 0
    breakdown: Dict[str, Dict] = None  # {толщина: {"length_m": метры, "cost": стоимость}}

    def __post_init__(self):
        if self.breakdown is None:
            self.breakdown = {}


def calculate_edge_for_modules(
    modules: List[Dict],
    edge_preset: str = "kitchen_standard",
    edge_prices: Optional[Dict[str, float]] = None
) -> EdgeCalculation:
    """
    Расчёт кромки для списка модулей.

    Args:
        modules: список модулей с деталями
        edge_preset: имя пресета ("kitchen_standard", "improved", "premium")
        edge_prices: словарь {толщина: цена_за_метр} из прайса

    Returns:
        EdgeCalculation с разбивкой по толщинам
    """
    if edge_prices is None:
        edge_prices = {}  # Будет заполнено из прайса

    preset = EDGE_PRESETS.get(edge_preset, EDGE_PRESETS["kitchen_standard"])

    result = EdgeCalculation()
    edge_lengths = {}  # {толщина: метры}

    for module in modules:
        if "details" not in module:
            continue

        for detail in module["details"]:
            # Определяем торцы детали
            edges = _get_detail_edges(detail, module)

            for edge_type, length_mm in edges.items():
                thickness = None

                if edge_type == "visible":
                    thickness = preset.visible_edge
                elif edge_type == "hidden":
                    thickness = preset.hidden_edge

                if thickness and length_mm > 0:
                    length_m = length_mm / 1000
                    if thickness not in edge_lengths:
                        edge_lengths[thickness] = 0
                    edge_lengths[thickness] += length_m

    # Считаем стоимости
    for thickness, length_m in edge_lengths.items():
        cost = length_m * edge_prices.get(thickness, 0)
        result.breakdown[thickness] = {
            "length_m": round(length_m, 2),
            "cost": round(cost, 2)
        }
        result.total_cost += cost

    result.total_cost = round(result.total_cost, 2)
    return result


def _get_detail_edges(detail: Dict, module: Dict) -> Dict[str, float]:
    """
    Определяет типы торцов для детали и их длины.

    Returns:
        {"visible": длина_мм, "hidden": длина_мм}
    """
    width = detail.get("width_mm", 0)
    height = detail.get("height_mm", 0)

    # Для корпусной мебели обычно:
    # - 2 видимых торца (ширина и высота лицевой стороны)
    # - 2 скрытых торца (тыльная сторона и соединения)

    # Упрощённая логика - все торцы считаем видимыми,
    # если не указано иное в настройках модуля
    return {
        "visible": 2 * (width + height),  # периметр лицевой стороны
        "hidden": 2 * (width + height),   # периметр тыльной стороны
    }


def calculate_edge_for_detail(
    detail: Dict,
    preset_name: str = "kitchen_standard",
    edge_prices: Optional[Dict[str, float]] = None
) -> Dict[str, float]:
    """
    Расчёт кромки для одной детали.

    Returns:
        {"0.8": метры, "2": метры, ...}
    """
    if edge_prices is None:
        edge_prices = {}

    preset = EDGE_PRESETS.get(preset_name, EDGE_PRESETS["kitchen_standard"])

    edges = _get_detail_edges(detail, {})
    edge_lengths = {}

    for edge_type, length_mm in edges.items():
        thickness = None

        if edge_type == "visible":
            thickness = preset.visible_edge
        elif edge_type == "hidden":
            thickness = preset.hidden_edge

        if thickness:
            length_m = length_mm / 1000
            edge_lengths[thickness] = edge_lengths.get(thickness, 0) + length_m

    return edge_lengths