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
    settings: Optional[Dict] = None
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

    # 1. Генерируем детали из модулей
    modules_with_details = _generate_details_for_modules(modules)

    # 2. Расчёт листов ЛДСП/МДФ
    material_prices = _extract_material_prices(selected_materials)
    result.sheets_calc = calculate_sheets_for_modules(
        modules=modules_with_details,
        material_prices=material_prices
    )
    total_sheets = result.sheets_calc.total_sheets or 0
    avg_price = _get_average_sheet_price(material_prices)
    result.material_cost = float(total_sheets * avg_price)

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

    # 4. Расчёт фурнитуры
    hinge_price = _extract_hinge_price(selected_hardware)
    drawer_prices = _extract_drawer_prices(selected_hardware)
    # Бренд петель (FIRMAX по умолчанию — «правила заказчика», общие со сметой
    # quantity_calc); BLUM/HETTICH — высотная таблица (см. hinges_per_door).
    hinge_brand = DEFAULT_HINGE_BRAND
    _hw_cfg = (selected_hardware or {}).get("hinges")
    if isinstance(_hw_cfg, dict) and _hw_cfg.get("brand"):
        hinge_brand = str(_hw_cfg["brand"])
    result.hardware_calc = calculate_hardware_for_modules(
        modules=modules,
        hinge_price=hinge_price,
        drawer_prices=drawer_prices,
        hinge_brand=hinge_brand,
    )
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
        if "facades" not in module:
            continue

        for facade in module["facades"]:
            width_m = facade.get("width_mm", 0) / 1000
            height_m = facade.get("height_mm", 0) / 1000
            area_m2 = width_m * height_m

            # Для фасадов на заказ: цена × площадь
            total_cost += area_m2 * facade_price_per_m2

    return round(total_cost, 2)


def _extract_hinge_price(selected_hardware: Dict) -> float:
    """Извлекает цену петель."""
    if "hinges" in selected_hardware:
        hinges = selected_hardware["hinges"]
        if isinstance(hinges, dict) and "price" in hinges and hinges["price"] is not None:
            return float(hinges["price"])
    return 0


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