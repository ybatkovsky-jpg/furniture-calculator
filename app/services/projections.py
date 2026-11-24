"""Выбор одной проекции на многоракурсном листе.

На листе часто несколько видов одной кухни (спереди, сбоку, сверху). Модель
перечисляет двери со всех видов, и их ширины складываются в сумму больше
габарита — на `0002` покрытие выходило 116 %.

Правило: кластеризуем фасады по горизонтали (разрыв больше 12 % ширины листа
делит кластеры) и берём тот вид, чья сумма ширин ближе к габариту с чертежа.
На однопроекционном листе кластер один — ничего не меняется.
"""

from __future__ import annotations

from typing import Any

GAP_PCT = 12.0
IDEAL_MIN = 0.85
IDEAL_MAX = 1.15

VIEW_PRIORITY = {
    "elevation": 0,
    "plan": 1,
    "section": 2,
    "render": 3,
    "detail": 4,
}


def _field(facade: Any, name: str, default: float = 0.0) -> float:
    if isinstance(facade, dict):
        return float(facade.get(name, default) or default)
    return float(getattr(facade, name, default) or default)


def split_clusters(facades: list[Any], gap_pct: float = GAP_PCT) -> list[list[Any]]:
    """Разбить фасады на группы по горизонтальным разрывам."""
    if not facades:
        return []

    ordered = sorted(facades, key=lambda f: _field(f, "bbox_x_pct"))
    clusters: list[list[Any]] = [[ordered[0]]]
    for facade in ordered[1:]:
        previous = clusters[-1][-1]
        previous_right = _field(previous, "bbox_x_pct") + _field(previous, "bbox_w_pct")
        gap = _field(facade, "bbox_x_pct") - previous_right
        if gap > gap_pct:
            clusters.append([facade])
        else:
            clusters[-1].append(facade)
    return clusters


def select_main_projection(
    facades: list[Any],
    total_width_mm: float | None,
    gap_pct: float = GAP_PCT,
) -> tuple[list[Any], dict]:
    """Оставить фасады одной проекции — той, что лучше сходится с габаритом."""
    info: dict = {"clusters": 0, "chosen": 0, "widths_mm": [], "reason": "одна проекция"}
    clusters = split_clusters(facades, gap_pct)
    info["clusters"] = len(clusters)
    if len(clusters) <= 1:
        return facades, info

    widths = [sum(_field(f, "width_mm") for f in cluster) for cluster in clusters]
    info["widths_mm"] = [round(width) for width in widths]

    total = float(total_width_mm or 0)
    if total > 0:
        def score(index: int) -> tuple[int, float]:
            ratio = widths[index] / total
            ideal = 0 if IDEAL_MIN <= ratio <= IDEAL_MAX else 1
            return (ideal, abs(ratio - 1.0))

        best = min(range(len(clusters)), key=score)
        info["reason"] = (
            f"выбрана проекция с шириной {widths[best]:.0f} мм "
            f"при габарите {total:.0f} мм"
        )
    else:
        best = max(range(len(clusters)), key=lambda index: widths[index])
        info["reason"] = "габарит неизвестен — взята самая широкая проекция"

    info["chosen"] = best
    return clusters[best], info


def _module_fingerprint(module: Any) -> tuple:
    if isinstance(module, dict):
        facades = module.get("facades") or {}
        drawers = module.get("drawers") or {}
        return (
            module.get("type"),
            module.get("width"),
            module.get("depth"),
            module.get("height"),
            facades.get("count") if isinstance(facades, dict) else 0,
            drawers.get("count") if isinstance(drawers, dict) else 0,
        )
    facades = getattr(module, "facades", None) or {}
    drawers = getattr(module, "drawers", None) or {}
    return (
        getattr(module, "type", None),
        getattr(module, "width", None),
        getattr(module, "depth", None),
        getattr(module, "height", None),
        facades.get("count") if isinstance(facades, dict) else 0,
        drawers.get("count") if isinstance(drawers, dict) else 0,
    )


def _width_sum(room: Any) -> float:
    return sum(
        float(getattr(module, "width", 0) or 0)
        * max(1, int(getattr(module, "quantity", 1) or 1))
        for module in getattr(room, "modules", []) or []
    )


def mark_duplicate_projection_rooms(rooms: list[Any]) -> list[Any]:
    """Пометить комнаты-дубли одной и той же мебели в разных проекциях.

    Модель B: страницы с одинаковыми `room_id` и `view_group` считаются
    одной мебелью. Главная проекция — фасад, затем план, разрез, рендер.
    Если состав совпадает хотя бы на 80 %, вторая комната помечается
    `is_duplicate_view=True` и не должна попадать в отдельный расчёт.
    """
    groups: dict[tuple, list[Any]] = {}
    for room in rooms or []:
        view_group = getattr(room, "view_group", None)
        if not view_group:
            continue
        room_key = getattr(room, "room_id", None) or getattr(room, "room_name", "")
        groups.setdefault((room_key, view_group), []).append(room)

    for group_rooms in groups.values():
        if len(group_rooms) < 2:
            continue

        def rank(room: Any) -> tuple:
            view_type = str(getattr(room, "view_type", "") or "")
            priority = VIEW_PRIORITY.get(view_type, 9)
            modules = list(getattr(room, "modules", []) or [])
            width = sum(float(getattr(module, "width", 0) or 0) for module in modules)
            return (priority, -len(modules), -width)

        main = min(group_rooms, key=rank)
        main_set = {_module_fingerprint(module) for module in main.modules}
        for room in group_rooms:
            if room is main or not main_set:
                continue
            current = {_module_fingerprint(module) for module in room.modules}
            if not current:
                continue
            overlap = len(current & main_set) / min(len(current), len(main_set))
            if overlap >= 0.8:
                room.is_duplicate_view = True
                room.duplicate_of_page = getattr(main, "page", None)
                room.projection_warnings.append(
                    f"Та же мебель, что на стр. {main.page}; "
                    "в расчёт не суммируется"
                )
            elif overlap > 0:
                room.projection_warnings.append(
                    "Состав частично совпадает с другой проекцией — проверьте"
                )

        plan = next(
            (
                room
                for room in group_rooms
                if str(getattr(room, "view_type", "") or "") == "plan"
            ),
            None,
        )
        if plan is not None and plan is not main:
            main_width = _width_sum(main)
            plan_width = _width_sum(plan)
            if main_width > 0 and plan_width > 0:
                difference = abs(main_width - plan_width) / max(main_width, plan_width)
                if difference > 0.10:
                    message = (
                        f"Ширина плана {plan_width:.0f} мм отличается от "
                        f"фасада {main_width:.0f} мм на {difference * 100:.0f}%"
                    )
                    main.projection_warnings.append(message)
                    plan.projection_warnings.append(message)
    return rooms

