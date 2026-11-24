"""Технологический BOM: детали, торцы и операции (этап 3).

`Part` — физическая деталь, а не строка «лист ЛДСП». Для каждой детали
фиксируются материал, размеры, количество, направление текстуры, кромка по
каждому торцу и операции присадки.

Правила намеренно отделены от распознавания: AI не решает, где боковина,
а где полка. Модуль описывается технологическими полями.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SAW_KERF_MM = 4
LDSP_THICKNESS_MM = 16.0
MDF_THICKNESS_MM = 18.0
BACK_PANEL_THICKNESS_MM = 3.0
EDGE_VISIBLE_MM = 0.8
EDGE_INVISIBLE_MM = 0.4


@dataclass
class EdgeOperation:
    """Обработка одного торца детали."""

    side: str  # front | back | left | right | top | bottom
    edge_type: str = "ПВХ"
    thickness_mm: float = EDGE_INVISIBLE_MM
    length_mm: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Part:
    """Физическая деталь модуля."""

    id: str
    module_id: str
    name: str
    material: str
    width_mm: int
    height_mm: int
    thickness_mm: float
    quantity: int = 1
    grain_direction: str | None = None
    edge_operations: list[EdgeOperation] = field(default_factory=list)
    source_rule: str = "carcass"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["edge_operations"] = [
            edge.as_dict() for edge in self.edge_operations
        ]
        return data


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _count(value: Any) -> int:
    if isinstance(value, dict):
        return max(0, _int(value.get("count")))
    if isinstance(value, list):
        return len(value)
    return max(0, _int(value))


def _edges(
    width_mm: int,
    height_mm: int,
    *,
    visible_sides: tuple[str, ...] = ("front",),
    visible_mm: float = EDGE_VISIBLE_MM,
) -> list[EdgeOperation]:
    """Кромка по всем торцам: видимые 0,8, остальные 0,4."""
    result = []
    for side, length in (
        ("front", width_mm),
        ("back", width_mm),
        ("left", height_mm),
        ("right", height_mm),
    ):
        visible = side in visible_sides
        result.append(
            EdgeOperation(
                side=side,
                thickness_mm=visible_mm if visible else EDGE_INVISIBLE_MM,
                length_mm=max(0, length),
            )
        )
    return result


def _part(
    module: dict,
    *,
    suffix: str,
    name: str,
    material: str,
    width_mm: int,
    height_mm: int,
    thickness_mm: float = LDSP_THICKNESS_MM,
    quantity: int = 1,
    grain: str | None = None,
    visible_sides: tuple[str, ...] = ("front",),
    source_rule: str = "carcass",
    with_edges: bool = True,
    edge_mm: float = EDGE_VISIBLE_MM,
) -> Part:
    module_qty = max(1, _int(module.get("quantity"), 1))
    module_id = str(module.get("id") or module.get("module_id") or "module")
    return Part(
        id=f"{module_id}:{suffix}",
        module_id=module_id,
        name=name,
        material=material,
        width_mm=max(1, int(width_mm)),
        height_mm=max(1, int(height_mm)),
        thickness_mm=float(thickness_mm),
        quantity=max(1, quantity * module_qty),
        grain_direction=grain,
        edge_operations=(
            _edges(width_mm, height_mm, visible_sides=visible_sides, visible_mm=edge_mm)
            if with_edges
            else []
        ),
        source_rule=source_rule,
    )


def build_module_parts(
    module: dict,
    module_id: str | None = None,
) -> list[Part]:
    """Разложить модуль на физические детали.

    Поддерживаются корпуса, перегородки, цоколь, ящики, фасады, столешница
    и столы. Неизвестные поля не выдумываются: если `partitions` нет, деталь
    не появляется.
    """
    module_type = str(module.get("type") or "lower_base")
    mid = module_id or str(module.get("id") or module.get("module_id") or "module")
    module = {**module, "id": mid}
    width = _int(module.get("width") or module.get("width_mm"))
    depth = _int(module.get("depth") or module.get("depth_mm"))
    height = _int(module.get("height") or module.get("height_mm"))
    if width <= 0 or height <= 0 or depth < 0:
        return []

    w = width + SAW_KERF_MM
    d = depth + SAW_KERF_MM if depth else 0
    h = height + SAW_KERF_MM
    parts: list[Part] = []

    # Стол/стойка: только столешница и боковины, без дна/полок/стенки.
    if module_type in {"table", "desk"}:
        parts.append(
            _part(
                module,
                suffix="top",
                name="Столешница",
                material="ЛДСП",
                width_mm=w,
                height_mm=max(1, d or depth),
                thickness_mm=LDSP_THICKNESS_MM,
                visible_sides=("front", "left", "right"),
                source_rule="table_top",
            )
        )
        parts.append(
            _part(
                module,
                suffix="side",
                name="Боковина стола",
                material="ЛДСП",
                width_mm=max(1, d or depth),
                height_mm=h,
                quantity=2,
                visible_sides=("front",),
                source_rule="table_side",
            )
        )
        return parts

    parts.append(
        _part(
            module,
            suffix="side",
            name="Боковина",
            material="ЛДСП",
            width_mm=max(1, d),
            height_mm=h,
            quantity=2,
            visible_sides=("front",),
            source_rule="carcass_side",
        )
    )
    parts.append(
        _part(
            module,
            suffix="bottom",
            name="Дно",
            material="ЛДСП",
            width_mm=w,
            height_mm=max(1, d),
            visible_sides=("front",),
            source_rule="carcass_bottom",
        )
    )
    parts.append(
        _part(
            module,
            suffix="top",
            name="Крыша",
            material="ЛДСП",
            width_mm=w,
            height_mm=max(1, d),
            visible_sides=("front",),
            source_rule="carcass_top",
        )
    )

    shelves = max(0, _int(module.get("shelves")))
    drawers = _count(module.get("drawers"))
    facades = _count(module.get("facades"))
    if shelves == 0 and module_type in ("lower_base", "upper_base") and not (
        drawers > 0 and facades == 0
    ):
        shelves = 1
    if shelves:
        parts.append(
            _part(
                module,
                suffix="shelf",
                name="Полка",
                material="ЛДСП",
                width_mm=w,
                height_mm=max(1, d),
                quantity=shelves,
                visible_sides=("front",),
                source_rule="shelf",
            )
        )

    partitions = max(0, _int(module.get("partitions")))
    if partitions:
        parts.append(
            _part(
                module,
                suffix="partition",
                name="Перегородка",
                material="ЛДСП",
                width_mm=max(1, d),
                height_mm=h,
                quantity=partitions,
                visible_sides=("front",),
                source_rule="partition",
            )
        )

    plinth_height = max(0, _int(module.get("plinth_height"), 100))
    if module.get("has_plinth"):
        parts.append(
            _part(
                module,
                suffix="plinth",
                name="Цоколь",
                material="ЛДСП",
                width_mm=w,
                height_mm=max(1, plinth_height),
                visible_sides=("front", "left", "right"),
                source_rule="plinth",
            )
        )

    parts.append(
        Part(
            id=f"{module.get('id') or 'module'}:back",
            module_id=str(module.get("id") or "module"),
            name="Задняя стенка",
            material="ХДФ",
            width_mm=width,
            height_mm=height,
            thickness_mm=BACK_PANEL_THICKNESS_MM,
            quantity=max(1, _int(module.get("quantity"), 1)),
            source_rule="back_wall",
        )
    )

    facade_type = "МДФ"
    facade_is_glass = False
    facades_raw = module.get("facades")
    if isinstance(facades_raw, dict):
        facade_type = str(facades_raw.get("material") or facades_raw.get("type") or "МДФ")
        facade_is_glass = str(facades_raw.get("type") or "").lower() == "glass"
    if facades:
        door_w = max(1, (width - 3 * facades) // facades)
        door_h = max(1, height - 4)
        for index in range(facades):
            parts.append(
                _part(
                    module,
                    suffix=f"facade-{index + 1}",
                    name=f"Фасад {index + 1}",
                    material="Стекло" if facade_is_glass else facade_type,
                    width_mm=door_w,
                    height_mm=door_h,
                    thickness_mm=4.0 if facade_is_glass else MDF_THICKNESS_MM,
                    visible_sides=() if facade_is_glass else ("front", "back", "left", "right"),
                    edge_mm=1.0 if "мдф" in facade_type.lower() else EDGE_VISIBLE_MM,
                    source_rule="facade",
                )
            )

    if drawers:
        drawer_h = max(1, (height - 3 * drawers) // drawers)
        box_h = max(1, drawer_h - 20)
        for index in range(drawers):
            # Готовый ящик — покупная фурнитура, а не деталь ЛДСП.
            # Корпус ящика добавляем только по явному запросу технолога.
            if module.get("drawer_boxes"):
                parts.extend(
                    [
                        _part(
                            module,
                            suffix=f"drawer-{index + 1}-side",
                            name=f"Боковина ящика {index + 1}",
                            material="ЛДСП",
                            width_mm=max(1, d),
                            height_mm=box_h,
                            quantity=2,
                            source_rule="drawer_side",
                        ),
                        _part(
                            module,
                            suffix=f"drawer-{index + 1}-bottom",
                            name=f"Дно ящика {index + 1}",
                            material="ЛДСП",
                            width_mm=max(1, w - 20),
                            height_mm=max(1, d),
                            source_rule="drawer_bottom",
                        ),
                        _part(
                            module,
                            suffix=f"drawer-{index + 1}-back",
                            name=f"Задняя стенка ящика {index + 1}",
                            material="ЛДСП",
                            width_mm=max(1, w - 20),
                            height_mm=box_h,
                            source_rule="drawer_back",
                        ),
                    ]
                )
            parts.append(
                _part(
                    module,
                    suffix=f"drawer-{index + 1}-facade",
                    name=f"Фасад ящика {index + 1}",
                    material=facade_type,
                    width_mm=max(1, width - 3),
                    height_mm=drawer_h,
                    visible_sides=("front", "back", "left", "right"),
                    source_rule="drawer_facade",
                )
            )

    if module.get("countertop"):
        parts.append(
            _part(
                module,
                suffix="countertop",
                name="Столешница",
                material="Столешница",
                width_mm=w,
                height_mm=max(1, d),
                thickness_mm=38.0,
                visible_sides=("front", "left", "right"),
                source_rule="countertop",
            )
        )

    glass = module.get("glass")
    if glass and not facade_is_glass:
        glass_count = _count(glass) or 1
        parts.append(
            _part(
                module,
                suffix="glass",
                name="Стекло",
                material="Стекло",
                width_mm=max(1, width - 3),
                height_mm=max(1, height - 4),
                thickness_mm=4.0,
                quantity=glass_count,
                visible_sides=(),
                source_rule="glass",
                with_edges=False,
            )
        )

    if module.get("mirror"):
        parts.append(
            _part(
                module,
                suffix="mirror",
                name="Зеркало",
                material="Зеркало",
                width_mm=max(1, width - 3),
                height_mm=max(1, height - 4),
                thickness_mm=4.0,
                visible_sides=(),
                source_rule="mirror",
                with_edges=False,
            )
        )

    if module.get("profile") or str(module.get("handle_mode") or "") == "gola":
        parts.append(
            _part(
                module,
                suffix="profile",
                name="Профиль GOLA",
                material="Алюминий",
                width_mm=max(1, width),
                height_mm=max(1, 30),
                thickness_mm=1.5,
                visible_sides=(),
                source_rule="profile",
                with_edges=False,
            )
        )

    return parts


def edge_totals(parts: list[Part], reserve_rate: float = 1.0) -> dict[str, float]:
    """Метраж кромки по толщинам: {'0.4': м, '0.8': м}.

    `reserve_rate` — запас на обработку (старый расчёт использует 1.3).
    Высокие фасады >2000 мм уходят в 2 мм, как в старом `quantity_calc`.
    """
    totals: dict[str, float] = {}
    for part in parts:
        for edge in part.edge_operations:
            thickness = edge.thickness_mm
            if (
                part.name.startswith("Фасад")
                and part.height_mm > 2000
                and thickness == EDGE_VISIBLE_MM
            ):
                thickness = 2.0
            key = "1.0" if thickness == 1.0 else f"{thickness:g}"
            totals[key] = (
                totals.get(key, 0.0)
                + edge.length_mm * part.quantity * reserve_rate / 1000
            )
    return {key: round(value, 2) for key, value in sorted(totals.items())}


def parts_area_m2(parts: list[Part], material: str | None = None) -> float:
    """Площадь деталей в м², с учётом количества."""
    area = sum(
        part.width_mm * part.height_mm * part.quantity
        for part in parts
        if material is None or part.material == material
    )
    return round(area / 1_000_000, 4)


def parts_area_by_group(parts: list[Part]) -> dict[str, float]:
    """Площадь по группам листа: {'ЛДСП': 12.3, 'ХДФ': 1.1}."""
    result: dict[str, float] = {}
    for part in parts:
        group = material_group(part.material)
        result[group] = round(
            result.get(group, 0.0)
            + part.width_mm * part.height_mm * part.quantity / 1_000_000,
            4,
        )
    return dict(sorted(result.items()))


def material_group(material: str) -> str:
    """Свести материал PRO100 к группе листа."""
    low = str(material or "").lower()
    if "хдф" in low or "лхдф" in low:
        return "ХДФ"
    if "стекло" in low:
        return "Стекло"
    if "мдф" in low:
        return "МДФ"
    if "лдсп" in low or "ламарти" in low or "цвета" in low:
        return "ЛДСП"
    return str(material or "Прочее")


def parts_by_material(parts: list[Part]) -> dict[str, float]:
    """Площадь по исходным материалам (без сведения к группе)."""
    result: dict[str, float] = {}
    for part in parts:
        result[part.material] = round(
            result.get(part.material, 0.0)
            + part.width_mm * part.height_mm * part.quantity / 1_000_000,
            4,
        )
    return dict(sorted(result.items()))


def parts_from_details(
    details: list[dict],
    module_id: str = "pro100",
) -> list[Part]:
    """Превратить строки `details.txt` в детали без выдуманной кромки.

    Это вход эталонных проектов: толщина/размеры/количество есть, кромки нет,
    поэтому `edge_operations` пустой. Материал сохраняется как в PRO100.
    """
    parts: list[Part] = []
    for index, row in enumerate(details or [], start=1):
        width = _int(row.get("width_mm"))
        height = _int(row.get("height_mm"))
        thickness = float(row.get("thickness_mm") or 0)
        quantity = max(1, _int(row.get("quantity"), 1))
        if width <= 0 or height <= 0 or not 2 <= thickness <= 60:
            continue
        parts.append(
            Part(
                id=f"{module_id}:{index}",
                module_id=module_id,
                name=f"Деталь {index}",
                material=str(row.get("material") or "ЛДСП"),
                width_mm=width,
                height_mm=height,
                thickness_mm=thickness,
                quantity=quantity,
                source_rule="pro100_details",
            )
        )
    return parts
