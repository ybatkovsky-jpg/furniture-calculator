"""
Расчёт количества листов ЛДСП/МДФ.

Коэффициент использования листа: 0.85 (85%)

Формула:
кол-во_листов = ceil(суммарная_площадь_деталей / (площадь_листа × 0.85))
площадь_листа = 2800 × 2070 = 5.796 м²
полезная_площадь = 5.796 × 0.85 = 4.927 м²
"""

import math
from typing import Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class SheetCalculation:
    """Результат расчёта листов."""
    total_sheets: int = 0
    total_area_m2: float = 0
    utilization_rate: float = 0.85
    breakdown: Dict[str, Dict] = field(default_factory=dict)  # {материал: {"sheets": кол-во, "area_m2": площадь, "cost": стоимость}}


SHEET_DIMENSIONS = {
    "standard": {
        "width_mm": 2800,
        "height_mm": 2070,
        "area_m2": 5.796
    }
}


def calculate_sheets_for_modules(
    modules: List[Dict],
    material_prices: Optional[Dict[str, float]] = None,
    sheet_type: str = "standard"
) -> SheetCalculation:
    """
    Расчёт листов для модулей.

    Args:
        modules: список модулей с деталями
        material_prices: {материал: цена_за_лист}
        sheet_type: тип листа ("standard")

    Returns:
        SheetCalculation с разбивкой по материалам
    """
    if material_prices is None:
        material_prices = {}

    sheet = SHEET_DIMENSIONS[sheet_type]
    sheet_area = sheet["area_m2"]
    useful_area = sheet_area * 0.85

    result = SheetCalculation()
    material_areas = {}  # {материал: суммарная_площадь_м2}

    # Собираем площади по материалам
    for module in modules:
        if "details" not in module:
            continue

        for detail in module["details"]:
            material = detail.get("material", "unknown")
            width_m = detail.get("width_mm", 0) / 1000
            height_m = detail.get("height_mm", 0) / 1000
            area_m2 = width_m * height_m

            if material not in material_areas:
                material_areas[material] = 0
            material_areas[material] += area_m2

    # Считаем листы для каждого материала
    for material, area_m2 in material_areas.items():
        sheets_needed = math.ceil(area_m2 / useful_area)
        cost = sheets_needed * material_prices.get(material, 0)

        result.breakdown[material] = {
            "sheets": sheets_needed,
            "area_m2": round(area_m2, 3),
            "cost": round(cost, 2)
        }

        result.total_sheets += sheets_needed
        result.total_area_m2 += area_m2

    result.total_area_m2 = round(result.total_area_m2, 3)
    return result


def calculate_sheets_for_area(
    total_area_m2: float,
    material_price: float = 0,
    sheet_type: str = "standard"
) -> Dict:
    """
    Расчёт листов для заданной площади.

    Returns:
        {"sheets": кол-во, "area_m2": площадь, "cost": стоимость}
    """
    sheet = SHEET_DIMENSIONS[sheet_type]
    sheet_area = sheet["area_m2"]
    useful_area = sheet_area * 0.85

    sheets_needed = math.ceil(total_area_m2 / useful_area)
    cost = sheets_needed * material_price

    return {
        "sheets": sheets_needed,
        "area_m2": round(total_area_m2, 3),
        "cost": round(cost, 2),
        "sheet_area_m2": sheet_area,
        "useful_area_m2": useful_area
    }