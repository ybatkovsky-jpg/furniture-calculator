"""Строки себестоимости (CostLine) — P0-02.

Раньше чат собирал смету из legacy-функций и терял кромку, ящики, ручки, ХДФ,
GOLA, LED, столешницу, крепёж. Здесь каждая позиция `MaterialQuantities`
превращается в строку с количеством, единицей, ценой и источником цены.

Правило: если количество > 0, а цены нет — строка остаётся с суммой 0, но
помечается `price_source="missing"` и попадает в `missing`/`warnings`.
Выпуск КП в таком состоянии блокируется (см. CalculationResult.can_issue_kp).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.domain.money import money_float, quantity_float, rub_float
from app.services.glass_calc import GlassCalculation
from app.services.quantity_calc import MaterialQuantities


def _round(value: float, digits: int = 2) -> float:
    """Деньги (digits=2) — HALF_UP; рубли (0) — HALF_UP."""
    if digits == 0:
        return rub_float(value)
    if digits == 2:
        return money_float(value)
    return quantity_float(value, digits)


def _to_float(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _price_from(selection: Any) -> Optional[float]:
    """Цена из выбранной позиции (dict) или списка позиций."""
    if isinstance(selection, dict):
        price = selection.get("price", selection.get("unit_price"))
        if price is not None:
            return _to_float(price)
    if isinstance(selection, list) and selection:
        prices = [
            _to_float(item.get("price", item.get("unit_price")))
            for item in selection
            if isinstance(item, dict)
        ]
        prices = [p for p in prices if p > 0]
        if prices:
            return sum(prices) / len(prices)
    return None


def _unit_from(selection: Any) -> str:
    if isinstance(selection, dict):
        return str(selection.get("price_unit") or selection.get("unit") or "")
    return ""


@dataclass
class CostLine:
    """Одна строка себестоимости."""

    kind: str          # ldsp | edge | facade | hdf | hinge | drawer | handle |
                       # gola | led | countertop | fastener | glass
    name: str
    quantity: float
    unit: str
    unit_price: float
    amount: float
    price_source: str  # selected | price_book | included | missing
    note: str = ""


@dataclass
class CostLineResult:
    """Результат построения строк + блокирующие причины."""

    lines: list[CostLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    handles_missing: bool = False

    @property
    def can_issue_kp(self) -> bool:
        return not self.missing and not self.handles_missing

    def sum_by_kind(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for line in self.lines:
            totals[line.kind] = totals.get(line.kind, 0.0) + line.amount
        return {k: _round(v) for k, v in totals.items()}

    @property
    def total(self) -> float:
        return _round(sum(line.amount for line in self.lines))


def _edge_price_map(selection: Any) -> dict[float, float]:
    """{толщина_мм: цена_за_метр} из выбранной кромки (dict или список)."""
    if isinstance(selection, list):
        result: dict[float, float] = {}
        for item in selection:
            result.update(_edge_price_map(item))
        return result
    price = _price_from(selection)
    if price is None or price <= 0:
        return {}
    thickness = None
    if isinstance(selection, dict):
        thickness = selection.get("thickness_mm")
        if thickness is None and selection.get("thickness") is not None:
            thickness = selection.get("thickness")
        name = str(selection.get("name") or "")
        if thickness is None and name:
            from app.services.price_manager import detect_thickness_mm

            thickness = detect_thickness_mm(name, "Кромка")
    try:
        thickness_value = float(thickness) if thickness is not None else 0.8
    except (TypeError, ValueError):
        thickness_value = 0.8
    return {round(thickness_value, 2): price}


def _drawer_unit_price(selection: Any) -> Optional[float]:
    """Цена ящика: UI хранит dict, старые данные — list (P0-02)."""
    return _price_from(selection)


def build_cost_lines(
    quantities: MaterialQuantities,
    selected_materials: dict[str, Any],
    selected_hardware: dict[str, Any],
    price_book: Optional[dict[str, float]] = None,
    glass_calc: Optional[GlassCalculation] = None,
    glass_items: Optional[list] = None,
) -> CostLineResult:
    """Собрать полный набор строк себестоимости из количеств."""
    result = CostLineResult()
    selected_materials = selected_materials or {}
    selected_hardware = selected_hardware or {}
    price_book = price_book or {}

    def add(
        kind: str,
        name: str,
        quantity: float,
        unit: str,
        price: Optional[float],
        source: str,
        block: bool = True,
        min_price: float = 0.0,
    ) -> None:
        quantity = _to_float(quantity)
        if quantity <= 0:
            return
        price_value = _to_float(price) if price is not None else 0.0
        if price_value <= 0 or (min_price and price_value < min_price):
            reason = (
                "подозрительно низкая цена"
                if price_value > 0
                else "цена не задана"
            )
            result.lines.append(
                CostLine(kind, name, quantity, unit, price_value, 0.0, "missing", reason)
            )
            if block:
                result.missing.append(f"{name} ({quantity:g} {unit})")
                result.warnings.append(f"{reason.capitalize()}: {name}")
            return
        result.lines.append(
            CostLine(
                kind, name, _round(quantity, 3), unit, price_value,
                _round(quantity * price_value), source,
            )
        )

    # ── ЛДСП (листы) ───────────────────────────────────────────────
    # P1-23: при нескольких ЛДСП не усредняем цены. Распределения листов по
    # материалам пока нет — берём цену основного и предупреждаем.
    ldsp = selected_materials.get("ldsp")
    if isinstance(ldsp, list) and ldsp:
        ldsp_price = _price_from(ldsp[0]) if isinstance(ldsp[0], dict) else None
        ldsp_unit = _unit_from(ldsp[0])
        if len(ldsp) > 1:
            result.warnings.append(
                "Выбрано несколько ЛДСП: использована цена основного материала, "
                "проверьте стоимость листов"
            )
        ldsp_label = str(ldsp[0].get("name", "")) if isinstance(ldsp[0], dict) else ""
    else:
        ldsp_price = _price_from(ldsp)
        ldsp_unit = _unit_from(ldsp)
        ldsp_label = str(ldsp.get("name", "")) if isinstance(ldsp, dict) else ""
    if ldsp_price and ldsp_unit in {"м²", "м2", "м"}:
        result.missing.append("ЛДСП: цена должна быть за лист")
        result.warnings.append(
            f"ЛДСП: цена указана за «{ldsp_unit}», а расход считается листами"
        )
    else:
        add(
            "ldsp",
            f"ЛДСП {ldsp_label}".strip(),
            quantities.ldsp_sheets,
            "лист",
            ldsp_price,
            "selected",
            min_price=100.0,
        )

    # ── ХДФ (задние стенки) ────────────────────────────────────────
    add(
        "hdf",
        "ХДФ (задние стенки)",
        quantities.hdf_sheets,
        "лист",
        price_book.get("hdf"),
        "price_book",
    )

    # ── Кромка по толщинам ─────────────────────────────────────────
    edge_prices = _edge_price_map(selected_materials.get("edge"))
    for thickness, metres, label in (
        (0.4, quantities.edge_04_m, "Кромка 0,4 мм"),
        (0.8, quantities.edge_08_m, "Кромка 0,8 мм"),
        (2.0, quantities.edge_2_m, "Кромка 2 мм"),
        (1.0, quantities.edge_mdf_1mm_m, "Кромка МДФ 1 мм"),
    ):
        if metres <= 0:
            continue
        price = edge_prices.get(thickness)
        source = "selected"
        if not price:
            # Цена по толщине из прайса (часто выбрана одна позиция кромки,
            # а раскрой требует 0.4/0.8/2 мм).
            book_key = (
                "edge_mdf_1mm" if thickness == 1.0 else f"edge_{thickness:g}"
            )
            price = price_book.get(book_key)
            if price:
                source = "price_book"
        # Бот выбирает одну позицию кромки, а раскрой требует 0.4/0.8/2 мм.
        # Для ЛДСП-кромки берём цену ближайшей выбранной толщины и
        # предупреждаем (МДФ-кромка так не подменяется — иначе неверный тип).
        if not price and edge_prices and thickness != 1.0:
            nearest = min(edge_prices, key=lambda t: abs(t - thickness))
            price = edge_prices[nearest]
            result.warnings.append(
                f"Кромка {thickness:g} мм: цена взята по {nearest:g} мм "
                "(проверьте прайс по толщинам)"
            )
        add(
            "edge", f"{label} ({thickness:g} мм)", metres, "м",
            price, source, min_price=1.0,
        )

    # ── Фасады ─────────────────────────────────────────────────────
    facades = selected_materials.get("facades")
    facade_price = _price_from(facades)
    facade_unit = _unit_from(facades)
    if facade_price and facade_unit in {"лист", "шт"}:
        result.missing.append("Фасады: цена должна быть за м²")
        result.warnings.append(
            f"Фасады: цена указана за «{facade_unit}», а расход считается в м²"
        )
    else:
        add(
            "facade",
            f"Фасады {facades.get('name', '') if isinstance(facades, dict) else ''}".strip(),
            round(quantities.facades_area_m2, 3),
            "м²",
            facade_price,
            "selected",
        )

    # ── Фурнитура ──────────────────────────────────────────────────
    hinges = selected_hardware.get("hinges")
    add(
        "hinge",
        f"Петли {hinges.get('brand', '') if isinstance(hinges, dict) else ''}".strip(),
        quantities.hinges_count,
        "шт",
        _price_from(hinges),
        "selected",
    )

    add(
        "drawer",
        "Ящики",
        quantities.drawers_count,
        "шт",
        _drawer_unit_price(selected_hardware.get("drawers")),
        "selected",
    )

    handles_sel = selected_hardware.get("handles")
    handles_price = _price_from(handles_sel)
    if quantities.handles_count > 0 and not handles_price:
        result.handles_missing = True
        result.warnings.append(
            "Ручки не выбраны: укажите модель ручки — без цены КП не выпускается"
        )
        result.lines.append(
            CostLine(
                "handle",
                "Ручки (не выбраны)",
                quantities.handles_count,
                "шт",
                0.0,
                0.0,
                "missing",
                "модель не выбрана",
            )
        )
    else:
        add(
            "handle",
            "Ручки",
            quantities.handles_count,
            "шт",
            handles_price,
            "selected",
        )

    # ── GOLA / LED (кухня) ─────────────────────────────────────────
    add(
        "gola",
        "GOLA горизонтальный (3 м)",
        quantities.gola_horizontal_pcs,
        "шт",
        price_book.get("gola_horizontal"),
        "price_book",
    )
    add(
        "gola",
        "GOLA вертикальный (3 м)",
        quantities.gola_vertical_pcs,
        "шт",
        price_book.get("gola_vertical"),
        "price_book",
    )

    add(
        "led",
        "LED-лента",
        quantities.led_strip_m,
        "м",
        price_book.get("led_strip"),
        "price_book",
    )
    add(
        "led",
        "Блок питания LED",
        quantities.led_power_supply,
        "шт",
        price_book.get("led_power"),
        "price_book",
    )
    add(
        "led",
        "Датчик LED",
        quantities.led_sensor,
        "шт",
        price_book.get("led_sensor"),
        "price_book",
    )

    # ── Столешница ─────────────────────────────────────────────────
    add(
        "countertop",
        "Столешница",
        round(quantities.countertop_length_m, 3),
        "м",
        price_book.get("countertop"),
        "price_book",
    )

    # ── Крепёж ─────────────────────────────────────────────────────
    add(
        "fastener",
        "Конфирмат 7×50",
        quantities.confirmat_count,
        "шт",
        price_book.get("confirmat"),
        "price_book",
    )
    add(
        "fastener",
        "Регулируемая опора",
        quantities.adjustable_feet,
        "шт",
        price_book.get("adjustable_feet"),
        "price_book",
    )
    add(
        "fastener",
        "Настенный подвес",
        quantities.wall_mounts,
        "шт",
        price_book.get("wall_mount"),
        "price_book",
    )
    add(
        "fastener",
        "Цоколь ПВХ (4 м)",
        quantities.plinth_strips,
        "шт",
        price_book.get("plinth"),
        "price_book",
    )

    # ── Стекло ─────────────────────────────────────────────────────
    glass_items = glass_items or []
    glass_cost = float(glass_calc.total_cost) if glass_calc else 0.0
    glass_area = float(glass_calc.total_area_m2) if glass_calc else 0.0
    if glass_items and glass_cost <= 0:
        result.missing.append("Стекло: цена не задана")
        result.warnings.append("Стекло: для выбранных элементов нет цены")
    elif glass_cost > 0:
        result.lines.append(
            CostLine(
                "glass",
                "Стекло / зеркала",
                _round(glass_area, 3),
                "м²",
                0.0,
                _round(glass_cost),
                "included",
            )
        )

    return result
