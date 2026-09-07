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

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import logging

from app.services.cost_calc import calculate_cost, CostBreakdown, DiscountInfo
from app.services.sheet_calc import calculate_sheets_for_modules, SheetCalculation
from app.services.edge_calc import calculate_edge_for_modules, EdgeCalculation
from app.services.hardware_calc import (
    calculate_hardware_for_modules, HardwareCalculation, DEFAULT_HINGE_BRAND
)
from app.services.glass_calc import calculate_glass_for_modules, GlassCalculation

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
    modules: List[Dict],
    selected_materials: Dict[str, Any],
    selected_hardware: Dict[str, Any],
    glass_items: List[Dict],
    discount_info: Optional[DiscountInfo] = None,
    settings: Optional[Dict] = None,
    zone_type: Optional[str] = None,
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

    # Настройки по умолчанию
    if settings is None:
        settings = {
            "manufacturing_rate": 0.80,
            "installation_rate": 0.55,
            "design_rate": 0.10,
            "overhead_rate": 0.05,
            "profit_rate": 0.30,
            "tech_director_rate": 0.015,
            "manager_rate": 0.0,
            "designer_rate": 0.0,
            "measurement_fee": 2500,
            "delivery_fee": 6500,
            "delivery_fee_client": 10000,
            "noncash_markup": 1.13,
        }

    # 1. Генерируем детали из модулей (оставляем для совместимости)
    modules_with_details = _generate_details_for_modules(modules)

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
    if not zone_type:
        zone_type = _infer_zone_type(modules)
    quantities = calculate_quantities(
        recognized, "", material_names, zone_type=zone_type, hinge_brand=hinge_brand
    )

    material_prices = _extract_material_prices(selected_materials)
    result.sheets_calc.total_sheets = quantities.ldsp_sheets
    result.sheets_calc.total_area_m2 = round(quantities.ldsp_area_m2, 3)
    avg_price = _get_average_sheet_price(material_prices)
    result.material_cost = float((quantities.ldsp_sheets or 0) * avg_price)

    # Количества прочих позиций (ХДФ, подсветка, GOLA, столешница) — для показа в смете
    result.hdf_sheets = quantities.hdf_sheets
    result.led_strip_m = quantities.led_strip_m
    result.gola_vertical_m = quantities.gola_vertical_m
    result.gola_horizontal_m = quantities.gola_horizontal_m
    result.countertop_length_m = quantities.countertop_length_m

    # 2. Расчёт кромки
    edge_prices = _extract_edge_prices(selected_materials)
    result.edge_calc = calculate_edge_for_modules(
        modules=modules,
        edge_preset="kitchen_standard",  # по умолчанию
        edge_prices=edge_prices
    )
    result.edge_cost = float(result.edge_calc.total_cost or 0)

    # 3. Расчёт фасадов
    result.facade_cost = _calculate_facades_cost(modules, selected_materials)

    # 4. Расчёт фурнитуры — количество из quantity_calc (петли + ручки + ящики)
    hinge_price = _extract_hinge_price(selected_hardware)
    drawer_prices = _extract_drawer_prices(selected_hardware)
    handle_price = _extract_handle_price(selected_hardware)
    drawer_avg = (sum(drawer_prices.values()) / len(drawer_prices)) if drawer_prices else 0.0
    result.hardware_calc = HardwareCalculation()
    result.hardware_calc.hinges_total = quantities.hinges_count
    result.hardware_calc.drawers_total = quantities.drawers_count
    result.hardware_calc.handles_total = quantities.handles_count
    result.hardware_calc.total_cost = round(
        quantities.hinges_count * hinge_price
        + quantities.drawers_count * drawer_avg
        + quantities.handles_count * handle_price,
        2,
    )
    result.hardware_calc.breakdown = {
        "hinges": {"total_hinges": quantities.hinges_count,
                   "cost": round(quantities.hinges_count * hinge_price, 2)},
        "drawers": {"total_drawers": quantities.drawers_count,
                    "cost": round(quantities.drawers_count * drawer_avg, 2)},
        "handles": {"total_handles": quantities.handles_count,
                    "cost": round(quantities.handles_count * handle_price, 2)},
    }
    result.hardware_cost = float(result.hardware_calc.total_cost or 0)

    # 5. Расчёт стекла
    # Преобразуем glass_items в формат для модулей
    modules_with_glass = _add_glass_to_modules(modules, glass_items)
    result.glass_calc = calculate_glass_for_modules(modules_with_glass)
    result.glass_cost = float(result.glass_calc.total_cost or 0)

    # 6. Дополнительные позиции (пока заглушки)
    result.lighting_cost = 0
    result.countertop_cost = 0
    result.accessories_cost = 0
    result.subcontractor_cost = 0

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
        # Коэффициенты
        manufacturing_rate=settings["manufacturing_rate"],
        installation_rate=settings["installation_rate"],
        design_rate=settings["design_rate"],
        overhead_rate=settings["overhead_rate"],
        profit_rate=settings["profit_rate"],
        tech_director_rate=settings["tech_director_rate"],
        manager_rate=settings["manager_rate"],
        designer_rate=settings["designer_rate"],
        measurement_fee=settings["measurement_fee"],
        delivery_fee=settings["delivery_fee"],
        delivery_fee_client=settings["delivery_fee_client"],
        noncash_markup=settings["noncash_markup"],
        # Скидки
        discount=discount
    )

    # Финальные цены
    result.total_cash = result.cost_breakdown.final_cash
    result.total_noncash = result.cost_breakdown.final_noncash

    return result


def _extract_material_prices(selected_materials: Dict) -> Dict[str, float]:
    """Извлекает цены на материалы из selected_materials."""
    prices = {}
    if "ldsp" in selected_materials:
        ldsp = selected_materials["ldsp"]
        if isinstance(ldsp, dict) and "price" in ldsp and ldsp["price"] is not None:
            prices[ldsp.get("name", "ldsp")] = float(ldsp["price"])
    return prices


def _extract_edge_prices(selected_materials: Dict) -> Dict[str, float]:
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


def _calculate_facades_cost(modules: List[Dict], selected_materials: Dict) -> float:
    """Расчёт стоимости фасадов."""
    total_cost = 0

    # Цена фасадов из selected_materials
    facade_price_per_m2 = 0
    if "facades" in selected_materials:
        facades = selected_materials["facades"]
        if isinstance(facades, dict) and "price" in facades and facades["price"] is not None:
            facade_price_per_m2 = float(facades["price"])

    for module in modules:
        facades = module.get("facades")
        if not facades:
            continue

        # Два формата фасадов:
        #  - dict {"count": N, "type": "doors"} (из RecognizedModule / _facades_to_modules);
        #  - list [{width_mm, height_mm}] (из _generate_details_for_modules).
        if isinstance(facades, dict):
            count = int(facades.get("count", 1) or 1)
            width_mm = (module.get("width", 0) or 0) / max(count, 1)
            height_mm = module.get("height", 0) or 0
            total_cost += (width_mm / 1000) * (height_mm / 1000) * count * facade_price_per_m2
        else:
            for facade in facades:
                width_m = (facade.get("width_mm", 0) or 0) / 1000
                height_m = (facade.get("height_mm", 0) or 0) / 1000
                total_cost += width_m * height_m * facade_price_per_m2

    return round(total_cost, 2)


def _extract_hinge_price(selected_hardware: Dict) -> float:
    """Извлекает цену петель."""
    if "hinges" in selected_hardware:
        hinges = selected_hardware["hinges"]
        if isinstance(hinges, dict) and "price" in hinges and hinges["price"] is not None:
            return float(hinges["price"])
    return 0


def _extract_handle_price(selected_hardware: Dict) -> float:
    """Извлекает цену ручки (опционально; если не задана — 0)."""
    hw = selected_hardware or {}
    handles = hw.get("handles")
    if isinstance(handles, dict) and handles.get("price") is not None:
        return float(handles["price"])
    if isinstance(handles, list) and handles and isinstance(handles[0], dict):
        return float(handles[0].get("price") or 0)
    return 0.0


def _extract_material_names(selected_materials: Dict) -> list:
    """Имена материалов (для quantity_calc.detect_material_properties)."""
    names = []
    if not isinstance(selected_materials, dict):
        return names
    for key in ("ldsp", "facades", "edge"):
        v = selected_materials.get(key)
        if isinstance(v, dict) and v.get("name"):
            names.append(str(v["name"]))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and item.get("name"):
                    names.append(str(item["name"]))
    return names


def _dict_to_recognized_module(m: Dict):
    """Преобразовать словарь модуля (из БД) в RecognizedModule для quantity_calc."""
    from app.services.image_analyzer import RecognizedModule
    return RecognizedModule(
        type=m.get("type", "lower_base"),
        width=int(m.get("width", 0) or 0),
        depth=int(m.get("depth", 0) or 0),
        height=int(m.get("height", 0) or 0),
        quantity=int(m.get("quantity", 1) or 1),
        has_glass=bool(m.get("has_glass", False)),
        facades=m.get("facades"),
        drawers=m.get("drawers"),
        shelves=int(m.get("shelves", 0) or 0),
        is_corner=bool(m.get("is_corner", False)),
    )


def _infer_zone_type(modules: List[Dict]) -> Optional[str]:
    """Грубая прикидка типа помещения: верхние базы → кухня (включает авто-GOLA/LED/столешницу)."""
    if any((m.get("type") or "") == "upper_base" for m in modules):
        return "Кухня"
    return None


def _extract_drawer_prices(selected_hardware: Dict) -> Dict[str, float]:
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


def _add_glass_to_modules(modules: List[Dict], glass_items: List[Dict]) -> List[Dict]:
    """Добавляет стекло в модули для расчёта."""
    # Упрощённо - добавляем всё стекло в первый модуль
    if not modules or not glass_items:
        return modules

    modules_copy = [module.copy() for module in modules]
    modules_copy[0]["glass_items"] = glass_items
    return modules_copy


def _generate_details_for_modules(modules: List[Dict]) -> List[Dict]:
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


def _get_average_sheet_price(material_prices: Dict[str, float]) -> float:
    """Средняя цена листа."""
    if not material_prices:
        return 0
    prices = [p for p in material_prices.values() if p is not None]
    if not prices:
        return 0
    return sum(prices) / len(prices)