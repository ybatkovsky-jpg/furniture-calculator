"""
Главный расчётный движок.

Связывает все компоненты расчёта:
- Деталировка модулей
- ЛДСП (sheet_calc)
- Кромка (edge_calc)
- Фасады
- Фурнитура (hardware_calc)
- Стекло (glass_calc)
- Финальная смета (cost_calc)
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.bom import ModuleBOM, build_bom
from app.domain.cost_lines import CostLine, build_cost_lines
from app.domain.nesting import NestingResult, nest_bom
from app.services.cost_calc import CostBreakdown, DiscountInfo, calculate_cost
from app.services.edge_calc import EdgeCalculation
from app.services.glass_calc import GlassCalculation, calculate_glass_for_modules
from app.services.hardware_calc import DEFAULT_HINGE_BRAND, HardwareCalculation
from app.services.sheet_calc import SheetCalculation

logger = logging.getLogger(__name__)


@dataclass
class CalculationResult:
    """Полный результат расчёта."""
    # Компоненты стоимости
    material_cost: float = 0
    edge_cost: float = 0
    facade_cost: float = 0
    hardware_cost: float = 0
    glass_cost: float = 0
    lighting_cost: float = 0
    countertop_cost: float = 0
    accessories_cost: float = 0
    subcontractor_cost: float = 0
    fastener_cost: float = 0

    # Полные строки себестоимости (P0-02): что именно и по какой цене
    lines: list[CostLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_prices: list[str] = field(default_factory=list)
    can_issue_kp: bool = True

    # S5-01/S5-03: деталировка и заготовка раскроя (сохраняются в quote)
    bom: list[ModuleBOM] = field(default_factory=list)
    nesting: NestingResult | None = None

    # P1-10: снимок цен и коэффициентов, по которому посчитан результат.
    price_book: dict[str, float] = field(default_factory=dict)
    policy: dict[str, float] = field(default_factory=dict)

    # Количества прочих позиций из quantity_calc (для показа в смете)
    hdf_sheets: int = 0
    led_strip_m: float = 0
    gola_vertical_m: float = 0
    gola_horizontal_m: float = 0
    countertop_length_m: float = 0

    # Детальные расчёты
    sheets_calc: SheetCalculation = None
    edge_calc: EdgeCalculation = None
    hardware_calc: HardwareCalculation = None
    glass_calc: GlassCalculation = None
    cost_breakdown: CostBreakdown = None

    # Финальные цены
    total_cash: float = 0
    total_noncash: float = 0

    def __post_init__(self):
        if self.sheets_calc is None:
            self.sheets_calc = SheetCalculation()
        if self.edge_calc is None:
            self.edge_calc = EdgeCalculation()
        if self.hardware_calc is None:
            self.hardware_calc = HardwareCalculation()
        if self.glass_calc is None:
            self.glass_calc = GlassCalculation()
        if self.cost_breakdown is None:
            self.cost_breakdown = CostBreakdown()


def calculate_full_cost(
    modules: list[dict],
    selected_materials: dict[str, Any],
    selected_hardware: dict[str, Any],
    glass_items: list[dict],
    discount_info: DiscountInfo | None = None,
    settings: dict | None = None,
    zone_type: str | None = None,
    price_book: dict[str, float] | None = None,
    has_spec: bool = False,
) -> CalculationResult:
    """
    Полный расчёт стоимости заказа.

    Args:
        modules: список модулей с деталями
        selected_materials: выбранные материалы {"ldsp": {...}, "facades": {...}}
        selected_hardware: выбранная фурнитура {"hinges": {...}, "drawers": {...}}
        glass_items: элементы стекла
        discount_info: информация о скидках/наценках
        settings: настройки расчёта (коэффициенты)

    Returns:
        CalculationResult с полной разбивкой
    """
    result = CalculationResult()

    # P1-09: единая политика коэффициентов (CalcSettings → config), без
    # третьей копии чисел в этом файле.
    from app.services.pricing_policy import coerce_policy

    policy = coerce_policy(settings)

    # 2. ЕДИНЫЙ ИСТОЧНИК ПРАВДЫ: количество ДСП и фурнитуры считает quantity_calc
    #    (та же логика, что в Excel: запас на раскрой, экономия смежных стенок,
    #    ручки, направляющие). Цены по-прежнему из прайса (selected_*).
    from app.services.quantity_calc import calculate_quantities
    hinge_brand = DEFAULT_HINGE_BRAND
    _hw_cfg = (selected_hardware or {}).get("hinges")
    if isinstance(_hw_cfg, dict) and _hw_cfg.get("brand"):
        hinge_brand = str(_hw_cfg["brand"])
    recognized = [_dict_to_recognized_module(m) for m in modules]
    material_names = _extract_material_names(selected_materials)
    ldsp_name = _extract_ldsp_name(selected_materials)
    drawer_system = _drawer_system_from_selection(selected_hardware)
    handle_mode = _handle_mode_from_selection(selected_hardware)
    if not zone_type:
        zone_type = _infer_zone_type(modules)
    quantities = calculate_quantities(
        recognized,
        "",
        material_names,
        ldsp_name=ldsp_name,
        drawer_system=drawer_system,
        handle_mode=handle_mode,
        zone_type=zone_type,
        hinge_brand=hinge_brand,
        has_spec=has_spec,
        # P1-02: платные автопозиции — только по явному выбору оператора,
        # а не молча по умолчанию (раньше +36 366 ₽ на кухню).
        auto_accessories=False,
        auto_drawers=False,
        auto_led=False,
        auto_gola=False,
    )

    result.sheets_calc.total_sheets = quantities.ldsp_sheets
    result.sheets_calc.total_area_m2 = round(quantities.ldsp_area_m2, 3)

    # Количества прочих позиций (ХДФ, подсветка, GOLA, столешница) — для показа в смете
    result.hdf_sheets = quantities.hdf_sheets
    result.led_strip_m = quantities.led_strip_m
    result.gola_vertical_m = quantities.gola_vertical_m
    result.gola_horizontal_m = quantities.gola_horizontal_m
    result.countertop_length_m = quantities.countertop_length_m

    # 3. Расчёт стекла.
    # P1-19: если размеры стекла не переданы, но у модулей есть
    # has_glass/фасады — стекло считается по геометрии дверей, а не 0 ₽.
    glass_items = list(glass_items or [])
    if not glass_items:
        glass_items = _derive_glass_items(modules)
    modules_with_glass = _add_glass_to_modules(modules, glass_items)
    result.glass_calc = calculate_glass_for_modules(modules_with_glass)
    result.glass_cost = float(result.glass_calc.total_cost or 0)

    # 4. Полный CostBreakdown: каждая позиция — строка с ценой и источником
    # (P0-02). Кромка берётся из quantities, edge_calc больше не участвует.
    lines_result = build_cost_lines(
        quantities,
        selected_materials or {},
        selected_hardware or {},
        price_book=price_book,
        glass_calc=result.glass_calc,
        glass_items=glass_items,
    )
    result.lines = lines_result.lines
    result.warnings = list(quantities.warnings) + list(lines_result.warnings)
    result.missing_prices = list(lines_result.missing)
    result.can_issue_kp = lines_result.can_issue_kp
    result.bom = build_bom(modules)
    result.price_book = dict(price_book or {})
    result.policy = dict(policy.as_dict())
    # S5-03: раскрой по габариту листа выбранного бренда ЛДСП.
    from app.services.quantity_calc import SHEET_LDSP_BY_BRAND

    ldsp_sheet = SHEET_LDSP_BY_BRAND.get(
        quantities.ldsp_brand, SHEET_LDSP_BY_BRAND["default"]
    )
    result.nesting = nest_bom(
        result.bom,
        sheet_sizes={"ЛДСП": (ldsp_sheet["width_mm"], ldsp_sheet["height_mm"])},
    )
    # P1-17: тип фасада не выбран — КП не выпускаем до явного выбора.
    if any("Тип фасада не выбран" in warning for warning in quantities.warnings):
        result.can_issue_kp = False

    totals = lines_result.sum_by_kind()
    result.material_cost = round(totals.get("ldsp", 0.0) + totals.get("hdf", 0.0), 2)
    result.edge_cost = round(totals.get("edge", 0.0), 2)
    result.facade_cost = round(totals.get("facade", 0.0), 2)
    result.hardware_cost = round(
        totals.get("hinge", 0.0) + totals.get("drawer", 0.0)
        + totals.get("handle", 0.0) + totals.get("drawer_internal", 0.0)
        + totals.get("push_open", 0.0) + totals.get("lift", 0.0),
        2,
    )
    result.glass_cost = round(totals.get("glass", result.glass_cost), 2)
    result.lighting_cost = round(totals.get("led", 0.0), 2)
    result.countertop_cost = round(totals.get("countertop", 0.0), 2)
    # P1-09: штанги, сушки, лотки, бутылочницы, гигиенические поддоны —
    # теперь входят в себестоимость, а не «есть только в Excel».
    result.accessories_cost = round(
        totals.get("gola", 0.0)
        + totals.get("rod", 0.0)
        + totals.get("bottle_holder", 0.0)
        + totals.get("cutlery_tray", 0.0)
        + totals.get("drying_rack", 0.0)
        + totals.get("hygienic_mat", 0.0),
        2,
    )
    result.fastener_cost = round(totals.get("fastener", 0.0), 2)
    result.subcontractor_cost = 0.0

    # Детальные расчёты — для совместимости со старым отображением.
    result.edge_calc = EdgeCalculation(total_cost=result.edge_cost)
    result.edge_calc.breakdown = {
        line.name: {"length_m": line.quantity, "cost": line.amount}
        for line in result.lines
        if line.kind == "edge"
    }
    result.hardware_calc = HardwareCalculation()
    result.hardware_calc.hinges_total = quantities.hinges_count
    result.hardware_calc.drawers_total = quantities.drawers_count
    result.hardware_calc.handles_total = quantities.handles_count
    result.hardware_calc.total_cost = result.hardware_cost
    result.hardware_calc.breakdown = {
        "hinges": {"total_hinges": quantities.hinges_count,
                   "cost": totals.get("hinge", 0.0)},
        "drawers": {"total_drawers": quantities.drawers_count,
                    "cost": totals.get("drawer", 0.0)},
        "handles": {"total_handles": quantities.handles_count,
                    "cost": totals.get("handle", 0.0)},
    }

    # 7. Итоговый расчёт через cost_calc
    discount = discount_info or DiscountInfo()

    result.cost_breakdown = calculate_cost(
        material_cost=result.material_cost,
        edge_cost=result.edge_cost,
        facade_cost=result.facade_cost,
        hardware_cost=result.hardware_cost,
        glass_cost=result.glass_cost,
        lighting_cost=result.lighting_cost,
        countertop_cost=result.countertop_cost,
        accessories_cost=result.accessories_cost,
        subcontractor_cost=result.subcontractor_cost,
        fastener_cost=result.fastener_cost,
        # Коэффициенты
        manufacturing_rate=policy.manufacturing_rate,
        installation_rate=policy.installation_rate,
        design_rate=policy.design_rate,
        overhead_rate=policy.overhead_rate,
        profit_rate=policy.profit_rate,
        tech_director_rate=policy.tech_director_rate,
        manager_rate=policy.manager_rate,
        designer_rate=policy.designer_rate,
        measurement_fee=policy.measurement_fee,
        delivery_fee=policy.delivery_fee,
        delivery_fee_client=policy.delivery_fee_client,
        noncash_markup=policy.noncash_markup,
        min_margin_rate=policy.min_margin_rate,
        # Скидки
        discount=discount
    )

    # P1-02: предупреждения финансовой формулы (например, «ниже полных
    # затрат») должны доходить до чата/КП, а не только лежать в breakdown.
    result.warnings.extend(result.cost_breakdown.warnings)
    if result.cost_breakdown.below_cost:
        result.can_issue_kp = False

    # Финальные цены
    result.total_cash = result.cost_breakdown.final_cash
    result.total_noncash = result.cost_breakdown.final_noncash

    return result


def _extract_material_prices(selected_materials: dict) -> dict[str, float]:
    """Извлекает цены на материалы из selected_materials."""
    prices = {}
    if "ldsp" in selected_materials:
        ldsp = selected_materials["ldsp"]
        if isinstance(ldsp, dict) and "price" in ldsp and ldsp["price"] is not None:
            prices[ldsp.get("name", "ldsp")] = float(ldsp["price"])
    return prices


def _extract_edge_prices(selected_materials: dict) -> dict[str, float]:
    """Извлекает цены на кромку."""
    prices = {}
    if "edge" in selected_materials:
        edge = selected_materials["edge"]
        if isinstance(edge, dict) and "price" in edge and edge["price"] is not None:
            # Определяем толщину из имени или используем по умолчанию
            name = edge.get("name", "").lower()
            thickness = "0.8"  # по умолчанию
            if "0.4" in name:
                thickness = "0.4"
            elif "2" in name or "hpl" in name:  # HPL часто 2мм
                thickness = "2"
            prices[thickness] = float(edge["price"])
    return prices


def _calculate_facades_cost(modules, selected_materials: dict) -> float:
    """Расчёт стоимости фасадов по НОРМАЛИЗОВАННЫМ модулям (RecognizedModule).

    Площадь одной двери считается по той же формуле, что в quantity_calc
    (зазор 3 мм по ширине на дверь и 4 мм по высоте), чтобы смета в чате и
    Excel не расходились. Модули без фасадов (кровать/стол/техника) пропускаются.
    """
    total_cost = 0

    # Цена фасадов из selected_materials
    facade_price_per_m2 = 0
    if "facades" in selected_materials:
        facades = selected_materials["facades"]
        if isinstance(facades, dict) and "price" in facades and facades["price"] is not None:
            facade_price_per_m2 = float(facades["price"])

    for module in modules:
        facades = getattr(module, "facades", None)
        if not isinstance(facades, dict) or facades.get("count", 0) <= 0:
            continue
        count = int(facades.get("count", 1) or 1)
        quantity = max(int(getattr(module, "quantity", 1) or 1), 1)
        width_mm = max(int(getattr(module, "width", 0) or 0) - 3 * count, 0)
        height_mm = max(int(getattr(module, "height", 0) or 0) - 4, 0)
        door_area_m2 = (width_mm / count / 1000) * (height_mm / 1000)
        total_cost += door_area_m2 * count * quantity * facade_price_per_m2

    return round(total_cost, 2)


def _extract_hinge_price(selected_hardware: dict) -> float:
    """Извлекает цену петель."""
    if "hinges" in selected_hardware:
        hinges = selected_hardware["hinges"]
        if isinstance(hinges, dict) and "price" in hinges and hinges["price"] is not None:
            return float(hinges["price"])
    return 0


def _extract_handle_price(selected_hardware: dict) -> float:
    """Извлекает цену ручки (опционально; если не задана — 0)."""
    hw = selected_hardware or {}
    handles = hw.get("handles")
    if isinstance(handles, dict) and handles.get("price") is not None:
        return float(handles["price"])
    if isinstance(handles, list) and handles and isinstance(handles[0], dict):
        return float(handles[0].get("price") or 0)
    return 0.0


def _drawer_system_from_selection(selected_hardware: dict) -> str | None:
    """Система ящиков по выбранной позиции (P1-06).

    `selected_hardware["drawers"]` — dict (UI) или список (старые данные).
    Имя позиции определяет и цену, и строку Excel; раньше выбор модели
    никак не влиял на `q.drawer_system` (всегда Tandembox).
    """
    if not isinstance(selected_hardware, dict):
        return None
    drawers = selected_hardware.get("drawers")
    name = ""
    if isinstance(drawers, dict):
        name = str(drawers.get("name") or "")
    elif isinstance(drawers, list) and drawers and isinstance(drawers[0], dict):
        name = str(drawers[0].get("name") or "")
    upper = name.upper()
    if "LEGRABOX" in upper:
        return "Legrabox"
    if "TANDEMBOX" in upper:
        return "Tandembox"
    if "BOYARD" in upper or "СТАРТ" in upper or "START" in upper:
        return "Boyard Start"
    return None


def _handle_mode_from_selection(selected_hardware: dict) -> str:
    """Способ открывания из выбора оператора (P1-07): handles/gola/push/lift."""
    if not isinstance(selected_hardware, dict):
        return "handles"
    mode = str(selected_hardware.get("handle_mode") or "handles").strip().lower()
    return mode if mode in {"handles", "gola", "push", "lift"} else "handles"


def _extract_ldsp_name(selected_materials: dict) -> str | None:
    """Имя выбранной позиции ЛДСП (P1-08).

    Бренд и формат листа определяются только по ней: раньше в объединённый
    список попадала кромка, и «кромка EGGER» подменяла бренд ЛДСП SLOTEX.
    """
    if not isinstance(selected_materials, dict):
        return None
    value = selected_materials.get("ldsp")
    if isinstance(value, dict):
        name = value.get("name")
        return str(name) if name else None
    if isinstance(value, list) and value and isinstance(value[0], dict):
        name = value[0].get("name")
        return str(name) if name else None
    return None


def _extract_material_names(selected_materials: dict) -> list:
    """Имена материалов для определения типа фасада/стекла.

    P1-08: кромка сюда НЕ входит — «Кромка ПВХ» ошибочно делала все фасады
    ПВХ, а бренд кромки подменял бренд ЛДСП. Для бренда ЛДСП есть отдельный
    `_extract_ldsp_name`.
    """
    names = []
    if not isinstance(selected_materials, dict):
        return names
    for key in ("ldsp", "facades"):
        v = selected_materials.get(key)
        if isinstance(v, dict) and v.get("name"):
            names.append(str(v["name"]))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and item.get("name"):
                    names.append(str(item["name"]))
    return names


def _dict_to_recognized_module(m: dict):
    """Словарь модуля (из БД / распознавания / ручного ввода) → RecognizedModule.

    В проекте исторически живут ДВЕ схемы модуля, и они по-разному трактуют
    одни и те же ключи:

    1. Распознавание (`image.py`, `recognition_cache.module_to_dict`) —
       {type, width, depth, height, quantity, facades: {"count": N},
        drawers: {"count": N}, shelves, has_glass, is_corner}, где
       **quantity — число одинаковых корпусов**, а фасады описаны отдельно.
    2. Ручной ввод (`bot/handlers/manual_input.py`) —
       {type, width, depth, height, filling: "doors"|"drawers"|"shelves"|…,
        quantity, has_glass}, где **quantity — число дверей/ящиков/полок**,
       а ключей facades/drawers нет вовсе.

    Третья, ещё более старая схема (деталировка/старые тесты) — размеры с
    суффиксом `_mm` и `facades` списком [{width_mm, height_mm}].

    Приведение здесь: `quantity` ручной схемы разворачивается в
    `facades.count`/`drawers.count`/`shelves`, а сама quantity становится 1
    (один корпус), иначе корпус размножался по числу дверей. Размеры
    принимаются в обоих вариантах записи, фасады-список сводятся к
    `{"count": len(list)}`.
    """
    from app.services.image_analyzer import RecognizedModule

    raw_filling = m.get("filling")
    filling = raw_filling.strip().lower() if isinstance(raw_filling, str) else ""

    has_glass = bool(m.get("has_glass", False))
    quantity = _pick_int(m, ("quantity",), 1)
    quantity = max(1, quantity)

    width = _pick_dim(m, "width", "width_mm")
    depth = _pick_dim(m, "depth", "depth_mm")
    height = _pick_dim(m, "height", "height_mm")

    facades = _normalize_facades(m.get("facades"), has_glass)
    drawers = _normalize_drawers(m.get("drawers"))
    shelves = max(0, _pick_int(m, ("shelves",), 0))

    # Ручная схема: заполнение и количество описаны через `filling`+`quantity`,
    # а не через facades/drawers. Разворачиваем и обнуляем множитель корпусов.
    if filling and facades is None and drawers is None:
        if filling == "doors":
            facades = {"count": quantity, "type": "glass" if has_glass else "doors"}
        elif filling == "drawers":
            drawers = {"count": quantity}
        elif filling == "shelves":
            shelves = quantity
        # lift / wine_rack — механизм/открытая вставка: фасадов-дверей нет
        quantity = 1
    # Если фасады заданы явно (распознавание) — доверяем им, quantity остаётся
    # числом корпусов.

    return RecognizedModule(
        type=m.get("type") or "lower_base",
        width=width,
        depth=depth,
        height=height,
        quantity=quantity,
        has_glass=has_glass,
        facades=facades,
        drawers=drawers,
        shelves=shelves,
        is_corner=bool(m.get("is_corner", False)),
        door_system=(str(m["door_system"]).lower() if m.get("door_system") else None),
        handle_mode=(str(m["handle_mode"]).lower() if m.get("handle_mode") else None),
    )


def _pick_dim(m: dict, *keys) -> int:
    """Размер в мм: принимает и `width`, и `width_mm`; нулевой ключ пропускает.

    Ноль первого ключа не должен «затирать» значение второго: при частичной
    миграции данных встречаются записи вида {"width": 0, "width_mm": 600}.
    """
    for key in keys:
        value = _pick_int(m, (key,), 0)
        if value > 0:
            return value
    return 0


def _pick_int(m: dict, keys, default: int) -> int:
    """Первое непустое целое из набора ключей ("600", "600 мм", 600.0 → 600)."""
    for key in keys:
        if key not in m or m[key] is None:
            continue
        value = m[key]
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
        digits = re.sub(r"[^\d]", "", str(value))
        if digits:
            try:
                return int(digits)
            except ValueError:
                continue
    return default


def _normalize_facades(raw, has_glass: bool = False):
    """Фасады → {"count": N, "type": "doors"|"glass"} (или None).

    Принимает: None; dict {"count"/"quantity", "type"}; список фасадов
    (старый формат [{width_mm, height_mm}] или список индексов и т.п.);
    просто число.
    """
    if raw is None:
        return None

    if isinstance(raw, dict):
        count = _pick_int(raw, ("count", "quantity", "n"), 1)
        if count <= 0:
            return None
        ftype = raw.get("type") or ("glass" if has_glass else "doors")
        return {"count": count, "type": str(ftype)}

    if isinstance(raw, (list, tuple)):
        if not raw:
            return None
        ftype = ""
        for item in raw:
            if isinstance(item, dict) and item.get("type"):
                ftype = str(item["type"])
                break
        if not ftype:
            ftype = "glass" if has_glass else "doors"
        return {"count": len(raw), "type": ftype}

    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        count = int(raw)
        return {"count": count, "type": "doors"} if count > 0 else None

    return None


def _normalize_drawers(raw):
    """Ящики → {"count": N, "type": ...} (или None). Список → len(списка)."""
    if raw is None:
        return None

    if isinstance(raw, dict):
        count = _pick_int(raw, ("count", "quantity", "n"), 1)
        if count <= 0:
            return None
        out = {"count": count}
        if raw.get("type"):
            out["type"] = str(raw["type"])
        return out

    if isinstance(raw, (list, tuple)):
        return {"count": len(raw)} if raw else None

    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        count = int(raw)
        return {"count": count} if count > 0 else None

    return None


def _infer_zone_type(modules: list[dict]) -> str | None:
    """Грубая прикидка типа помещения: верхние базы → кухня (включает авто-GOLA/LED/столешницу)."""
    if any((m.get("type") or "") == "upper_base" for m in modules):
        return "Кухня"
    return None


def _extract_drawer_prices(selected_hardware: dict) -> dict[str, float]:
    """Извлекает цены на ящики."""
    prices = {}
    if "drawers" in selected_hardware:
        drawers = selected_hardware["drawers"]
        if isinstance(drawers, list):
            for drawer in drawers:
                if "price" in drawer and drawer["price"] is not None:
                    key = f"{drawer.get('brand', 'UNKNOWN')} {drawer.get('type', 'standard')} {drawer.get('height_type', 'medium')} {drawer.get('runner_type', '450mm')}"
                    prices[key] = float(drawer["price"])
    return prices


def _derive_glass_items(modules: list[dict]) -> list[dict]:
    """Стеклянные фасады → элементы стекла по геометрии КАЖДОЙ двери (P1-19/P0-03).

    Ручной ввод и распознавание передают только `has_glass`, без размеров.
    Считаем площадь стекла по двери: (ширина/число дверей − 3 мм) ×
    (высота − 4 мм) — те же зазоры, что в quantity_calc. Тип «clear»;
    цена берётся из glass_calc.

    P0-03: элементов столько же, сколько дверей. Раньше на модуль создавался
    ОДИН элемент половинной ширины: две стеклянные двери 800×900 давали
    0,3557 м² вместо 0,7114 (недосчёт 50 %). `module_id`/`door_index`
    позволяют проверить, что одна дверь не оплачена дважды.
    """
    items: list[dict] = []
    for module_index, module in enumerate(modules or []):
        if not module.get("has_glass"):
            continue
        facades = module.get("facades")
        if isinstance(facades, dict):
            count = int(facades.get("count") or 0)
        elif isinstance(facades, list):
            count = len(facades)
        else:
            count = 0
        if count <= 0:
            continue
        width = _pick_dim(module, "width", "width_mm")
        height = _pick_dim(module, "height", "height_mm")
        if width <= 0 or height <= 0:
            continue
        door_width = max(width // count - 3, 0)
        door_height = max(height - 4, 0)
        for door_index in range(count):
            items.append(
                {
                    "type": "clear",
                    "width_mm": door_width,
                    "height_mm": door_height,
                    "tinting": False,
                    "module_id": module_index,
                    "door_index": door_index,
                }
            )
    return items


def _add_glass_to_modules(modules: list[dict], glass_items: list[dict]) -> list[dict]:
    """Привязать элементы стекла к своим модулям (P0-03).

    Раньше всё стекло сваливалось в ПЕРВЫЙ модуль. Для цены это было
    эквивалентно, но проверка «одна дверь — один тип оплаты» становилась
    невозможной: нельзя было сопоставить строку стекла с модулем/дверью.
    Элементы без `module_id` (старые явные `glass_items` из кэша) кладём
    в первый модуль со стеклом — обратная совместимость.
    """
    if not modules or not glass_items:
        return modules

    modules_copy = [module.copy() for module in modules]
    fallback = next(
        (index for index, module in enumerate(modules) if module.get("has_glass")),
        0,
    )
    by_module: dict[int, list[dict]] = {}
    for item in glass_items:
        module_id = item.get("module_id")
        if not isinstance(module_id, int) or not 0 <= module_id < len(modules_copy):
            module_id = fallback
        by_module.setdefault(module_id, []).append(item)

    for module_id, items in by_module.items():
        modules_copy[module_id]["glass_items"] = items
    return modules_copy


def _generate_details_for_modules(modules: list[dict]) -> list[dict]:
    """Генерирует детали для модулей."""
    modules_with_details = []

    for module in modules:
        module_copy = module.copy()
        details = []

        width = module.get("width", 0)
        depth = module.get("depth", 0)
        height = module.get("height", 0)
        filling = module.get("filling", "doors")
        quantity = module.get("quantity", 1)

        material = "ldsp"  # по умолчанию

        # Корпусные детали (всегда)
        if width > 0 and height > 0:
            # Задняя стенка
            details.append({
                "name": "Задняя стенка",
                "material": material,
                "width_mm": width,
                "height_mm": height
            })

        if depth > 0 and height > 0:
            # Боковины
            details.append({
                "name": "Левая боковина",
                "material": material,
                "width_mm": depth,
                "height_mm": height
            })
            details.append({
                "name": "Правая боковина",
                "material": material,
                "width_mm": depth,
                "height_mm": height
            })

        if width > 0 and depth > 0:
            # Дно
            details.append({
                "name": "Дно",
                "material": material,
                "width_mm": width,
                "height_mm": depth
            })

        # Наполнение
        if filling == "shelves" and quantity > 0 and width > 0 and depth > 0:
            # Полки
            for i in range(quantity):
                details.append({
                    "name": f"Полка {i+1}",
                    "material": material,
                    "width_mm": width,
                    "height_mm": depth
                })
        elif filling == "drawers":
            # Ящики - добавляем в module["drawers"]
            module_copy["drawers"] = []
            for i in range(quantity):
                module_copy["drawers"].append({
                    "type": "standard",
                    "brand": "BOYARD",  # по умолчанию
                    "height_type": "medium"
                })

        # Фасады
        if filling in ["doors", "drawers"]:
            # Добавляем фасады
            module_copy["facades"] = []
            facade_count = quantity if filling == "doors" else 1  # для ящиков обычно 1 фасад на модуль
            for i in range(facade_count):
                module_copy["facades"].append({
                    "width_mm": width,
                    "height_mm": height,
                    "type": "mdf" if not module.get("has_glass", False) else "glass"
                })

        module_copy["details"] = details
        modules_with_details.append(module_copy)

    return modules_with_details


def _get_average_sheet_price(material_prices: dict[str, float]) -> float:
    """Средняя цена листа."""
    if not material_prices:
        return 0
    prices = [p for p in material_prices.values() if p is not None]
    if not prices:
        return 0
    return sum(prices) / len(prices)