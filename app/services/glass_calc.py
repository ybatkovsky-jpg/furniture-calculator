"""
Расчёт стекла (ЧАСТЬ 5).

Типы с фиксированной ценой:
- clear (2500₽/м²), moru_riflenoe (4250₽/лист),
- for_aluminum (2230₽/м²), тонировка (+2000₽)

Типы БЕЗ цены (запросить у оператора):
- graphite, bronze, tempered

Фасады на заказ: цена_за_м² × площадь (подтверждено)
MORU: считается листами, с оптимизацией укладки деталей
"""

import math
from dataclasses import dataclass


@dataclass
class GlassCalculation:
    """Результат расчёта стекла."""
    total_area_m2: float = 0
    total_cost: float = 0
    breakdown: list[dict] = None  # список элементов стекла

    def __post_init__(self):
        if self.breakdown is None:
            self.breakdown = []


# Фиксированные цены на стекло
GLASS_PRICES = {
    "clear": 2500,  # ₽/м²
    "moru_riflenoe": 4250,  # ₽/лист (2000×3300)
    "for_aluminum": 2230,  # ₽/м²
    "tinting": 2000,  # ₽ за тонировку
}

# Размеры листа MORU
MORU_SHEET_SIZE = {
    "width_mm": 2000,
    "height_mm": 3300,
    "area_m2": 6.6
}


def calculate_glass_cost(
    glass_type: str,
    width_mm: float,
    height_mm: float,
    tinting: bool = False,
    manual_price: float | None = None
) -> dict:
    """
    Расчёт стоимости одного элемента стекла.

    Args:
        glass_type: тип стекла
        width_mm, height_mm: размеры
        tinting: нужна ли тонировка
        manual_price: цена, введённая оператором (для типов без фиксированной цены)

    Returns:
        {"area_m2": площадь, "cost": стоимость, "description": описание}
    """
    # Защита от отрицательных размеров: раньше они давали отрицательную
    # площадь и отрицательную стоимость в смете.
    if width_mm < 0 or height_mm < 0:
        raise ValueError("Размеры стекла не могут быть отрицательными")

    area_m2 = (width_mm * height_mm) / 1_000_000

    if glass_type == "moru_riflenoe":
        # MORU продаётся листами 2000×3300 (6.6 м²): считаем листы по площади.
        sheets_needed = math.ceil(area_m2 / MORU_SHEET_SIZE["area_m2"]) if area_m2 > 0 else 0
        cost = GLASS_PRICES["moru_riflenoe"] * sheets_needed
        description = f"MORU {width_mm}×{height_mm}мм ({sheets_needed} лист.)"

    elif glass_type in GLASS_PRICES:
        # Фиксированная цена за м²
        price_per_m2 = GLASS_PRICES[glass_type]
        cost = area_m2 * price_per_m2
        description = f"{glass_type} {width_mm}×{height_mm}мм ({area_m2:.3f}м²)"

    elif manual_price is not None:
        # Цена введена оператором
        cost = area_m2 * manual_price
        description = f"{glass_type} {width_mm}×{height_mm}мм ({area_m2:.3f}м²) - ручная цена"

    else:
        # Нет цены - нужно запросить у оператора
        cost = 0
        description = f"{glass_type} {width_mm}×{height_mm}мм - нужна цена"

    # Добавляем тонировку
    if tinting:
        cost += GLASS_PRICES["tinting"]
        description += " + тонировка"

    return {
        "area_m2": round(area_m2, 3),
        "cost": round(cost, 2),
        "description": description,
        "glass_type": glass_type,
        "tinting": tinting
    }


def calculate_glass_for_modules(modules: list[dict]) -> GlassCalculation:
    """
    Расчёт стекла для всех модулей.

    Модули содержат glass_items: [{
        "type": "clear",
        "width_mm": 600,
        "height_mm": 800,
        "tinting": false,
        "manual_price": null  # если введена оператором
    }]
    """
    result = GlassCalculation()

    for module in modules:
        if "glass_items" not in module:
            continue

        for glass_item in module["glass_items"]:
            glass_calc = calculate_glass_cost(
                glass_type=glass_item["type"],
                width_mm=glass_item["width_mm"],
                height_mm=glass_item["height_mm"],
                tinting=glass_item.get("tinting", False),
                manual_price=glass_item.get("manual_price")
            )

            result.breakdown.append(glass_calc)
            result.total_area_m2 += glass_calc["area_m2"]
            result.total_cost += glass_calc["cost"]

    result.total_area_m2 = round(result.total_area_m2, 3)
    result.total_cost = round(result.total_cost, 2)

    return result