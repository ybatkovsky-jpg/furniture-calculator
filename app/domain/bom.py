"""Деталировка модулей до отдельной детали (S5-01, заготовка BOM).

Задача этапа 5 — перейти от «листов ЛДСП» к списку деталей: боковины, дно,
крыша, полки, задняя стенка, фасады, ящики. Здесь строится базовый BOM по
габаритам модуля; он сохраняется в `Calculation.quote["bom"]` и может быть
использован для раскроя/nesting без изменения текущего расчёта количеств.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SAW_KERF_MM = 4  # припуск на пропил, как в quantity_calc
BACK_PANEL_THICKNESS_MM = 3.0
LDSP_THICKNESS_MM = 16.0
MDF_THICKNESS_MM = 18.0


@dataclass
class BOMLine:
    """Одна деталь в спецификации."""

    part: str
    material: str
    width_mm: int
    height_mm: int
    quantity: int = 1
    thickness_mm: float = LDSP_THICKNESS_MM
    source: str = "carcass"
    edge_m: float = 0.0


@dataclass
class ModuleBOM:
    """BOM одного модуля."""

    module_type: str
    width_mm: int
    depth_mm: int
    height_mm: int
    quantity: int
    lines: list[BOMLine] = field(default_factory=list)


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def build_module_bom(module: dict) -> ModuleBOM:
    """Деталировка одного модуля (dict из распознавания/ручного ввода)."""
    module_type = str(module.get("type") or "lower_base")
    width = _int(module.get("width") or module.get("width_mm"))
    depth = _int(module.get("depth") or module.get("depth_mm"))
    height = _int(module.get("height") or module.get("height_mm"))
    quantity = max(1, _int(module.get("quantity") or 1))

    result = ModuleBOM(module_type, width, depth, height, quantity)
    if width <= 0 or depth <= 0 or height <= 0:
        return result

    # Габариты с пропилом
    w = width + SAW_KERF_MM
    d = depth + SAW_KERF_MM
    h = height + SAW_KERF_MM

    result.lines.extend([
        BOMLine("Боковина", "ЛДСП", d, h, 2, source="carcass"),
        BOMLine("Дно", "ЛДСП", w, d, 1, source="carcass"),
        BOMLine("Крыша", "ЛДСП", w, d, 1, source="carcass"),
    ])

    shelves = max(0, _int(module.get("shelves") or 0))
    if shelves == 0 and module_type in ("lower_base", "upper_base"):
        shelves = 1
    if shelves:
        result.lines.append(BOMLine("Полка", "ЛДСП", w, d, shelves, source="shelves"))

    # Задняя стенка — ХДФ без припуска на пропил
    result.lines.append(
        BOMLine(
            "Задняя стенка", "ХДФ", width, height, 1,
            thickness_mm=BACK_PANEL_THICKNESS_MM, source="back",
        )
    )

    facades = module.get("facades")
    facade_count = 0
    facade_type = "МДФ"
    if isinstance(facades, dict):
        facade_count = max(0, _int(facades.get("count")))
        if str(facades.get("type") or "").lower() == "glass":
            facade_type = "Стекло"
    elif isinstance(facades, list):
        facade_count = len(facades)
    if facade_count:
        door_w = max(1, (width - 3 * facade_count) // facade_count)
        door_h = max(1, height - 4)
        for index in range(facade_count):
            result.lines.append(
                BOMLine(
                    f"Фасад {index + 1}", facade_type, door_w, door_h, 1,
                    thickness_mm=MDF_THICKNESS_MM, source="facade",
                    edge_m=round(2 * (door_w + door_h) / 1000, 3),
                )
            )

    drawers = module.get("drawers")
    drawer_count = 0
    if isinstance(drawers, dict):
        drawer_count = max(0, _int(drawers.get("count")))
    elif isinstance(drawers, list):
        drawer_count = len(drawers)
    for index in range(drawer_count):
        result.lines.append(
            BOMLine(
                f"Фасад ящика {index + 1}", facade_type, width - 3, height - 4, 1,
                thickness_mm=MDF_THICKNESS_MM, source="drawer",
            )
        )

    return result


def build_bom(modules: list[dict]) -> list[ModuleBOM]:
    """BOM по всем модулям проекта."""
    return [build_module_bom(module) for module in modules or []]
