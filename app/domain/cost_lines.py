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
from typing import Any

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


def _price_from(selection: Any) -> float | None:
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
                       # gola | led | countertop | fastener | glass |
                       # rod | bottle_holder | cutlery_tray | drying_rack |
                       # hygienic_mat | drawer_internal
    name: str
    quantity: float
    unit: str
    unit_price: float
    amount: float
    price_source: str  # selected | price_book | included | missing
    note: str = ""
    # P0-03: к какому модулю/двери относится строка (стекло — позлементно).
    # Нужно, чтобы проверить «одна дверь не оплачена дважды».
    module_id: int | None = None
    door_index: int | None = None
    # P0-02: поле строки шаблона Excel (см. templates/row_mapping.json).
    # None — строки в шаблоне нет, документ покажет её в блоке «ПРОЧЕЕ».
    field: str | None = None


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


def _drawer_unit_price(selection: Any) -> float | None:
    """Цена ящика: UI хранит dict, старые данные — list (P0-02)."""
    return _price_from(selection)


def _ldsp_field(quantities: MaterialQuantities) -> str:
    """Поле шаблона для ЛДСП по бренду и текстуре (P0-02)."""
    brand = str(quantities.ldsp_brand or "").upper()
    texture = str(quantities.ldsp_material or "") == "текстура"
    if brand == "EXTRAVERT":
        return "ldsp_sheets_texture_extravert" if texture else "ldsp_sheets_plain_extravert"
    if brand == "LAMARTY":
        return "ldsp_sheets_texture_lamarty" if texture else "ldsp_sheets_plain_lamarty"
    if brand == "ТОМЛЕСДРЕВ":
        return "ldsp_sheets_tomlesdrev"
    return "ldsp_sheets_texture" if texture else "ldsp_sheets_plain"


def _facade_field(facades: Any) -> str | None:
    """Поле шаблона для фасада по названию/категории выбранной позиции."""
    if not isinstance(facades, dict):
        return None
    text = f"{facades.get('name', '')} {facades.get('category', '')}".upper()
    if "TITAN" in text:
        return "facades_m2_emdiway_titan"
    if "EMDIWAY" in text:
        return "facades_m2_emdiway"
    if "МАТОВ" in text or "ЛАКОКРАСК" in text:
        return "facades_m2_paint_matte"
    if "ГЛЯНЕЦ" in text or "ГЛЯНЦ" in text:
        return "facades_m2_paint_gloss"
    if "ПВХ" in text:
        return "facades_m2_pvh_s"
    return None


def _mdf_edge_field(facades: Any) -> str:
    """Поле кромки МДФ по бренду фасада (P0-06: одна позиция — одна строка)."""
    text = ""
    if isinstance(facades, dict):
        text = f"{facades.get('name', '')} {facades.get('category', '')}".upper()
    if "EMDIWAY" in text or "EMDIWEY" in text:
        return "edge_mdf_1mm_emdiway"
    if "EVOGLOSS" in text or "EVO " in text:
        return "edge_mdf_1mm_evo"
    if "AGT" in text:
        return "edge_mdf_1mm_agt"
    if "ETERNO" in text:
        return "edge_mdf_1mm_eterno"
    if "SM`ART" in text or "SMART" in text:
        return "edge_mdf_1mm_smart"
    return "edge_mdf_1mm_kromka"


def _hinge_field(hinges: Any) -> str | None:
    """Поле шаблона для петель: FIRMAX есть в шаблоне, BLUM/HETTICH — нет.

    Честнее показать BLUM/HETTICH в «ПРОЧЕЕ», чем молча записать их в строку
    FIRMAX (именно это занижало Excel, P0-02).
    """
    if not isinstance(hinges, dict):
        return None
    text = f"{hinges.get('brand', '')} {hinges.get('name', '')}".upper()
    if "FIRMAX" in text:
        return "hinges_firmax_closer"
    return None


def _drawer_field(system: str | None) -> str:
    """Поле шаблона для выбранной системы ящиков (P1-06)."""
    upper = str(system or "").upper()
    if "LEGRABOX" in upper:
        return "drawers_legrabox"
    if "TANDEMBOX" in upper:
        return "drawers_tandembox"
    return "drawers_boyard_start"


def build_cost_lines(
    quantities: MaterialQuantities,
    selected_materials: dict[str, Any],
    selected_hardware: dict[str, Any],
    price_book: dict[str, float] | None = None,
    glass_calc: GlassCalculation | None = None,
    glass_items: list | None = None,
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
        price: float | None,
        source: str,
        block: bool = True,
        min_price: float = 0.0,
        field: str | None = None,
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
                CostLine(
                    kind, name, quantity, unit, price_value, 0.0, "missing", reason,
                    field=field,
                )
            )
            if block:
                result.missing.append(f"{name} ({quantity:g} {unit})")
                result.warnings.append(f"{reason.capitalize()}: {name}")
            return
        result.lines.append(
            CostLine(
                kind, name, _round(quantity, 3), unit, price_value,
                _round(quantity * price_value), source, field=field,
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
            field=_ldsp_field(quantities),
        )

    # ── ХДФ (задние стенки) ────────────────────────────────────────
    add(
        "hdf",
        "ХДФ (задние стенки)",
        quantities.hdf_sheets,
        "лист",
        price_book.get("hdf"),
        "price_book",
        field="hdf_sheets",
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
        edge_field = {
            0.4: "edge_04_m",
            0.8: "edge_08_m",
            2.0: "edge_2_m",
            1.0: _mdf_edge_field(selected_materials.get("facades")),
        }[thickness]
        add(
            "edge", f"{label} ({thickness:g} мм)", metres, "м",
            price, source, min_price=1.0, field=edge_field,
        )

    # ── Фасады ─────────────────────────────────────────────────────
    facades = selected_materials.get("facades")
    facade_price = _price_from(facades)
    facade_unit = _unit_from(facades)
    facade_area = round(quantities.facades_area_m2, 3)
    facade_name = (
        f"Фасады {facades.get('name', '') if isinstance(facades, dict) else ''}"
    ).strip()
    if facade_price and facade_unit in {"лист", "шт"}:
        # P1-05: цена за лист, а расход фасада — в м². Пересчитываем через
        # формат листа из позиции прайса. Без формата — missing и блок КП,
        # а не молчаливый ноль или «примерно».
        width_mm = _to_float(facades.get("sheet_width_mm")) if isinstance(facades, dict) else 0.0
        height_mm = _to_float(facades.get("sheet_height_mm")) if isinstance(facades, dict) else 0.0
        if width_mm > 0 and height_mm > 0:
            sheet_area_m2 = width_mm * height_mm / 1_000_000
            price_per_m2 = _round(facade_price / sheet_area_m2)
            result.lines.append(
                CostLine(
                    "facade",
                    facade_name,
                    facade_area,
                    "м²",
                    price_per_m2,
                    _round(facade_area * price_per_m2),
                    "selected",
                    note=(
                        f"пересчёт с листа: {facade_price:,.2f} ₽ / "
                        f"{sheet_area_m2:.3f} м² ({width_mm:g}×{height_mm:g} мм)"
                    ),
                    field=_facade_field(facades),
                )
            )
            if facade_area <= 0:
                result.lines.pop()
        else:
            result.missing.append("Фасады: не задан формат листа")
            result.warnings.append(
                "Фасады: цена указана за лист, но формат листа не задан — "
                "пересчёт в м² невозможен, КП блокируется"
            )
            if facade_area > 0:
                result.lines.append(
                    CostLine(
                        "facade", facade_name, facade_area, "м²",
                        0.0, 0.0, "missing", "нужен формат листа",
                        field=_facade_field(facades),
                    )
                )
    else:
        add(
            "facade",
            facade_name,
            facade_area,
            "м²",
            facade_price,
            "selected",
            field=_facade_field(facades),
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
        field=_hinge_field(hinges),
    )

    add(
        "drawer",
        "Ящики",
        quantities.drawers_count,
        "шт",
        _drawer_unit_price(selected_hardware.get("drawers")),
        "selected",
        field=_drawer_field(quantities.drawer_system),
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
                field="handles_count",
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
            field="handles_count",
        )

    # ── GOLA / LED (кухня) ─────────────────────────────────────────
    add(
        "gola",
        "GOLA горизонтальный (3 м)",
        quantities.gola_horizontal_pcs,
        "шт",
        price_book.get("gola_horizontal"),
        "price_book",
        field="gola_horizontal_3m",
    )
    add(
        "gola",
        "GOLA вертикальный (3 м)",
        quantities.gola_vertical_pcs,
        "шт",
        price_book.get("gola_vertical"),
        "price_book",
        field="gola_vertical_3m",
    )

    add(
        "led",
        "LED-лента",
        quantities.led_strip_m,
        "м",
        price_book.get("led_strip"),
        "price_book",
        field="led_strip_5m",
    )
    add(
        "led",
        "Блок питания LED",
        quantities.led_power_supply,
        "шт",
        price_book.get("led_power"),
        "price_book",
        field="led_power_supply",
    )
    add(
        "led",
        "Датчик LED",
        quantities.led_sensor,
        "шт",
        price_book.get("led_sensor"),
        "price_book",
        field="led_sensor",
    )

    # ── Столешница ─────────────────────────────────────────────────
    add(
        "countertop",
        "Столешница",
        round(quantities.countertop_length_m, 3),
        "м",
        price_book.get("countertop"),
        "price_book",
        field="countertop_length_m",
    )

    # ── Крепёж ─────────────────────────────────────────────────────
    add(
        "fastener",
        "Конфирмат 7×50",
        quantities.confirmat_count,
        "шт",
        price_book.get("confirmat"),
        "price_book",
        field="confirmat_count",
    )
    add(
        "fastener",
        "Регулируемая опора",
        quantities.adjustable_feet,
        "шт",
        price_book.get("adjustable_feet"),
        "price_book",
        field="adjustable_feet",
    )
    add(
        "fastener",
        "Настенный подвес",
        quantities.wall_mounts,
        "шт",
        price_book.get("wall_mount"),
        "price_book",
        field="wall_mounts",
    )
    add(
        "fastener",
        "Цоколь ПВХ (4 м)",
        quantities.plinth_strips,
        "шт",
        price_book.get("plinth"),
        "price_book",
        field="plinth_strips",
    )

    # ── Комплектующие, которые раньше были только в Excel (P1-09) ──────
    # Правило: каждое поле MaterialQuantities с quantity > 0 получает строку.
    # Цены — из прайса; нет цены → missing и блокировка КП, а не молчаливый 0.
    add(
        "rod",
        "Штанга круглая D25",
        quantities.rods_round_count,
        "шт",
        price_book.get("rods_round"),
        "price_book",
        field="rods_round",
    )
    add(
        "rod",
        "Штанга прямоугольная (антискользящая)",
        quantities.rods_rectangular_count,
        "шт",
        price_book.get("rods_rectangular"),
        "price_book",
        field="rods_rectangular",
    )
    add(
        "bottle_holder",
        f"Бутылочница ({quantities.bottle_holder_type or 'Flora'})",
        quantities.bottle_holder_count,
        "шт",
        price_book.get("bottle_holder"),
        "price_book",
        field=(
            "bottle_holder_kvadro"
            if str(quantities.bottle_holder_type or "").lower() == "kvadro"
            else "bottle_holder_flora"
        ),
    )
    add(
        "cutlery_tray",
        "Лоток для столовых приборов",
        quantities.cutlery_tray_count,
        "шт",
        price_book.get("cutlery_tray"),
        "price_book",
        field="cutlery_tray",
    )
    add(
        "drying_rack",
        f"Сушка для посуды ({quantities.drying_rack_type or 'Alba'})",
        quantities.drying_rack_count,
        "шт",
        price_book.get("drying_rack"),
        "price_book",
        field=(
            "drying_rack_boyard"
            if str(quantities.drying_rack_type or "").lower() == "boyard"
            else "drying_rack_alba"
        ),
    )
    add(
        "hygienic_mat",
        "Гигиенический поддон / коврик",
        quantities.hygienic_mat_count,
        "шт",
        price_book.get("hygienic_mat"),
        "price_book",
        field="hygienic_mat",
    )
    add(
        "drawer_internal",
        "Ящик внутренний (для приборов)",
        quantities.drawers_internal_count,
        "шт",
        price_book.get("drawers_internal"),
        "price_book",
        field="drawers_internal_tandembox",
    )

    # ── Стекло ─────────────────────────────────────────────────────
    # P0-03: строка на КАЖДЫЙ элемент (дверь), а не одна на модуль.
    # Смешанное стекло (часть с ценой, часть без) больше не маскируется
    # ненулевым итогом: отсутствие цены хотя бы у одного элемента блокирует КП.
    glass_items = glass_items or []
    breakdown = list(getattr(glass_calc, "breakdown", None) or [])
    if breakdown:
        for element in breakdown:
            glass_type = str(element.get("glass_type") or "")
            area = _to_float(element.get("area_m2"))
            cost = _to_float(element.get("cost"))
            module_id = element.get("module_id")
            door_index = element.get("door_index")
            label = f"Стекло ({glass_type})" if glass_type else "Стекло / зеркала"
            note = str(element.get("description") or "")
            if cost <= 0:
                result.missing.append(f"{label} ({area:g} м²)")
                result.warnings.append(
                    f"Стекло: для элемента {area:g} м² нет цены"
                )
                result.lines.append(
                    CostLine(
                        "glass", label, _round(area, 3), "м²", 0.0, 0.0,
                        "missing", "цена не задана", module_id, door_index,
                    )
                )
                continue
            # MORU продаётся листами: цена за м² — справочная, сумма — по листам.
            unit_price = _round(cost / area, 2) if area > 0 else 0.0
            result.lines.append(
                CostLine(
                    "glass", label, _round(area, 3), "м²",
                    unit_price, _round(cost), "included", note,
                    module_id, door_index,
                )
            )
    elif glass_items and float(getattr(glass_calc, "total_cost", 0) or 0) <= 0:
        result.missing.append("Стекло: цена не задана")
        result.warnings.append("Стекло: для выбранных элементов нет цены")

    return result
