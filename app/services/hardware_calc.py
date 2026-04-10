"""
Расчёт фурнитуры (петли, ящики, направляющие).

Правила расчёта петель (ЧАСТЬ 10):
- высота < 900 мм → 2 петли
- 900-1200 мм → 3 петли
- 1200-1600 мм → 4 петли
- > 1600 мм → 5 петель

Ящики: по бренду, типу, глубине направляющей = глубина_модуля - 50 мм
"""

from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class HardwareCalculation:
    """Результат расчёта фурнитуры."""
    hinges_total: int = 0
    drawers_total: int = 0
    total_cost: float = 0
    breakdown: Dict[str, Dict] = None  # {"hinges": {...}, "drawers": {...}}

    def __post_init__(self):
        if self.breakdown is None:
            self.breakdown = {}


def calculate_hinges_for_modules(
    modules: List[Dict],
    hinge_price: float = 0
) -> Dict:
    """
    Расчёт петель для модулей с фасадами.

    Правила:
    - высота < 900 мм → 2 петли
    - 900-1200 мм → 3 петли
    - 1200-1600 мм → 4 петли
    - > 1600 мм → 5 петель

    Returns:
        {"total_hinges": кол-во, "cost": стоимость}
    """
    total_hinges = 0

    for module in modules:
        # Проверяем, есть ли фасады в модуле
        if "facades" not in module or not module["facades"]:
            continue

        for facade in module["facades"]:
            height_mm = facade.get("height_mm", 0)

            if height_mm < 900:
                hinges = 2
            elif height_mm <= 1200:
                hinges = 3
            elif height_mm <= 1600:
                hinges = 4
            else:
                hinges = 5

            total_hinges += hinges

    cost = total_hinges * hinge_price

    return {
        "total_hinges": total_hinges,
        "cost": round(cost, 2),
        "price_per_hinge": hinge_price
    }


def calculate_drawers_for_modules(
    modules: List[Dict],
    drawer_prices: Optional[Dict[str, float]] = None
) -> Dict:
    """
    Расчёт ящиков для модулей.

    Args:
        modules: список модулей
        drawer_prices: {тип_ящика: цена} из прайса

    Returns:
        {"total_drawers": кол-во, "cost": стоимость, "breakdown": {...}}
    """
    if drawer_prices is None:
        drawer_prices = {}

    total_drawers = 0
    total_cost = 0
    breakdown = {}

    for module in modules:
        # Проверяем, есть ли ящики в модуле
        if "drawers" not in module or not module["drawers"]:
            continue

        module_depth = module.get("depth_mm", 560)  # по умолчанию 560 для нижних
        runner_depth = module_depth - 50  # направляющая = глубина модуля - 50мм

        # Определяем тип направляющей по глубине
        if runner_depth <= 300:
            runner_type = "250mm"
        elif runner_depth <= 400:
            runner_type = "350mm"
        elif runner_depth <= 500:
            runner_type = "450mm"
        elif runner_depth <= 600:
            runner_type = "550mm"
        else:
            runner_type = "550mm"  # максимум

        for drawer in module["drawers"]:
            drawer_type = drawer.get("type", "standard")
            brand = drawer.get("brand", "BOYARD")
            height_type = drawer.get("height_type", "medium")  # low, medium, high

            # Формируем ключ для цены: "BRAND TYPE HEIGHT RUNNER"
            price_key = f"{brand} {drawer_type} {height_type} {runner_type}"

            price = drawer_prices.get(price_key, 0)
            total_drawers += 1
            total_cost += price

            # Добавляем в разбивку
            if price_key not in breakdown:
                breakdown[price_key] = {"count": 0, "price": price, "cost": 0}
            breakdown[price_key]["count"] += 1
            breakdown[price_key]["cost"] += price

    return {
        "total_drawers": total_drawers,
        "cost": round(total_cost, 2),
        "breakdown": breakdown
    }


def calculate_hardware_for_modules(
    modules: List[Dict],
    hinge_price: float = 0,
    drawer_prices: Optional[Dict[str, float]] = None
) -> HardwareCalculation:
    """
    Полный расчёт фурнитуры для модулей.
    """
    result = HardwareCalculation()

    # Петли
    hinges_data = calculate_hinges_for_modules(modules, hinge_price)
    result.hinges_total = hinges_data["total_hinges"]
    result.breakdown["hinges"] = hinges_data

    # Ящики
    drawers_data = calculate_drawers_for_modules(modules, drawer_prices)
    result.drawers_total = drawers_data["total_drawers"]
    result.breakdown["drawers"] = drawers_data

    # Общая стоимость
    result.total_cost = round(hinges_data["cost"] + drawers_data["cost"], 2)

    return result