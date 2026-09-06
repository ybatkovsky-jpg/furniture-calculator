"""
Расчёт фурнитуры (петли, ящики, направляющие, подъёмники, штанги).

Правила расчёта петель (ЧАСТЬ 10) — единая точка hinges_per_door():
- FIRMAX («правила заказчика», дефолт): ТОЛЬКО высота двери
  ≥2000 → 4; 901–1999 → 3; ≤900 → 2 (вес игнорируется).
- BLUM/HETTICH без веса (door_weight_kg не задан/0):
  >1600 → 5; 1200–1600 → 4; 900–1200 → 3; <900 → 2.
- BLUM/HETTICH с известным весом фасада: та же высотная база + весовая
  надбавка +1 петля, если фасад «тяжёлый» для своей высоты (таблица порогов
  HINGE_WEIGHT_RULES_BY_BRAND: до 900мм и >18кг; от 900мм и >20кг).
  Источник — типовые рекомендации BLUM/Hettich: до ~900мм высоты и ≤18кг —
  2 петли; высокие (>1600мм) или тяжёлые (>18–20кг) фасады → +1 петля
  к высотному минимуму. Вес — ОПЦИОНАЛЬНЫЙ вход: если неизвестен (0/None),
  поведение ровно как раньше (чисто высотная таблица).

Ящики: по бренду, типу, глубине направляющей = глубина_модуля - 50 мм
Подъёмники: BLUM HF (складной), BLUM HK-XS (поворотный), DTC Top Stay
Штанги: круглая D25, прямоугольная антискользящая
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


# ═══════════════════════════════════════════════════════════════════
# ЕДИНОЕ ПРАВИЛО ПЕТЕЛЬ (по высоте ОДНОЙ двери)
# ═══════════════════════════════════════════════════════════════════

# Нисходящий список порогов: (мин_высота_мм, петель_на_дверь).
# FIRMAX — «правила заказчика» (Excel-смета AI-пайплайна, quantity_calc):
#   ≥2000 → 4; 901..1999 → 3; ≤900 → 2.
# BLUM/HETTICH — высотная таблица (используется, когда вес фасада неизвестен):
#   >1600 → 5; 1200..1600 → 4; 900..1200 → 3; <900 → 2.
HINGE_RULES_BY_BRAND: Dict[str, tuple] = {
    "FIRMAX": ((2000, 4), (901, 3), (0, 2)),
    "BLUM": ((1601, 5), (1200, 4), (900, 3), (0, 2)),
    "HETTICH": ((1601, 5), (1200, 4), (900, 3), (0, 2)),
}
DEFAULT_HINGE_BRAND = "FIRMAX"

# ═══════════════════════════════════════════════════════════════════
# ВЕСОВАЯ НАДБАВКА ДЛЯ BLUM/HETTICH (вес фасада — ОПЦИОНАЛЬНЫЙ вход)
# ═══════════════════════════════════════════════════════════════════

# Когда вес фасада известен (door_weight_kg > 0), тяжёлый фасад при той же
# высоте требует на 1 петлю больше, чем по чисто высотной таблице.
# Нисходящий список порогов: (мин_высота_мм, вес_порог_кг) — если высота двери
# ≥ мин_высота (первое подходящее правило, как в высотной таблице) И вес двери
# > вес_порог → +1 петля к высотному минимуму.
#
# Источник — типовые рекомендации BLUM (каталоги «Подбор петель по высоте/весу
# фасада», BLUM/Hettich): до ~900 мм высоты и ≤18 кг → достаточно 2 петель;
# высокие (>1600 мм) или тяжёлые (>18–20 кг) фасады требуют +1 петлю.
#   <900 мм:    2 базовых → 3, если >18 кг
#   900–1600 мм: 3–4 базовых → +1, если >20 кг
#   >1600 мм:   5 базовых → 6, если >20 кг
# FIRMAX — «правила заказчика»: только по высоте, вес НЕ учитывается никогда.
HINGE_WEIGHT_RULES_BY_BRAND: Dict[str, tuple] = {
    "FIRMAX": (),
    "BLUM": ((900, 20.0), (0, 18.0)),
    "HETTICH": ((900, 20.0), (0, 18.0)),
}


def hinges_per_door(
    door_height_mm: float,
    brand: str = DEFAULT_HINGE_BRAND,
    door_weight_kg: float = None,
) -> int:
    """Сколько петель ставить на ОДНУ дверь высотой door_height_mm.

    Единая точка истины для сметного пайплайна (quantity_calc) и
    калькулятора стоимости (calc_engine → hardware_calc), чтобы оба потока
    давали одинаковое число петель на один и тот же набор дверей.
    Неизвестный бренд → DEFAULT_HINGE_BRAND.

    door_weight_kg — ОПЦИОНАЛЬНЫЙ вес двери (кг):
    - 0/None (неизвестен) → чисто высотная таблица (поведение как раньше);
    - для BLUM/HETTICH при известном весе тяжёлый фасад получает +1 петлю
      (HINGE_WEIGHT_RULES_BY_BRAND);
    - для FIRMAX вес игнорируется: «правила заказчика» только по высоте.
    """
    brand_key = (brand or "").upper()
    rules = HINGE_RULES_BY_BRAND.get(brand_key, HINGE_RULES_BY_BRAND[DEFAULT_HINGE_BRAND])
    for min_h, count in rules:
        if door_height_mm >= min_h:
            return count + _heavy_facade_bonus(
                door_height_mm, door_weight_kg, brand_key
            )
    return rules[-1][1] + _heavy_facade_bonus(
        door_height_mm, door_weight_kg, brand_key
    )


def _heavy_facade_bonus(door_height_mm: float, door_weight_kg: float, brand_key: str) -> int:
    """+1 петля для BLUM/HETTICH, если известен вес и фасад «тяжёлый».

    FIRMAX и неизвестные бренды → 0 (правила заказчика: только по высоте).
    """
    if brand_key not in ("BLUM", "HETTICH"):
        return 0
    if not door_weight_kg or door_weight_kg <= 0:
        return 0  # вес неизвестен — чисто высотная таблица
    weight_rules = HINGE_WEIGHT_RULES_BY_BRAND[brand_key]
    for min_h, weight_threshold in weight_rules:
        if door_height_mm >= min_h:
            return 1 if door_weight_kg > weight_threshold else 0
    return 0


# ═══════════════════════════════════════════════════════════════════
# ОЦЕНКА ВЕСА ФАСАДА (для высотно-весовой логики BLUM/HETTICH)
# ═══════════════════════════════════════════════════════════════════

# Плотности материалов фасадов (кг/м³).
# Источник: справочные плотности плит/материалов:
#   ЛДСП ~680–700 кг/м³, МДФ ~750–800 кг/м³, стекло ~2500 кг/м³.
# Берём середину диапазона: ЛДСП 700, МДФ 780, стекло 2500.
FACADE_MATERIAL_DENSITY_KG_M3: Dict[str, float] = {
    "ldsp": 700.0,    # ЛДСП (в т.ч. ПВХ-фасады на ЛДСП)
    "mdf": 780.0,     # МДФ (в т.ч. крашеный/EMDIWAY)
    "glass": 2500.0,  # стекло
}

# Типовая толщина фасада (мм) по материалу — используется ТОЛЬКО для оценки
# веса, когда точная толщина фасада не вводится в смете. Реальные фасады:
# ЛДСП-фасады 16мм, МДФ-фасады 18мм, стекло 4мм (в раме может быть толще).
FACADE_DEFAULT_THICKNESS_MM: Dict[str, float] = {
    "ldsp": 16.0,
    "mdf": 18.0,
    "glass": 4.0,
}


def _facade_material_key(material: str) -> str:
    """Приводит название материала фасада к ключу таблиц плотности/толщины.

    Поддерживает как ключи ("ldsp"/"mdf"/"glass"), так и русские/смешанные
    названия ("ЛДСП", "МДФ", "стекло", "зеркало"). Неизвестный материал → None
    (вес такого фасада считается неизвестным).
    """
    if not material:
        return None
    s = str(material).strip().lower()
    if s in ("ldsp", "лдсп"):
        return "ldsp"
    if s in ("mdf", "мдф"):
        return "mdf"
    if s in ("glass", "стекло"):
        return "glass"
    if any(w in s for w in ("стекл", "зеркал", "glass", "mirror")):
        return "glass"
    if any(w in s for w in ("лдсп", "ldsp", "пвх", "плёнк", "пленк")):
        return "ldsp"
    if any(w in s for w in ("мдф", "mdf", "эмал", "краск", "полиуретан", "акрил")):
        return "mdf"
    return None


def estimate_facade_weight_kg(
    width_mm: float,
    height_mm: float,
    thickness_mm: float = None,
    density_kg_m3: float = None,
    material: str = None,
) -> float:
    """Оценка веса ОДНОГО фасада (кг) по габаритам и материалу.

    масса = ширина×высота×толщина (м³) × плотность (кг/м³).

    Args:
        width_mm / height_mm: габариты фасада, мм.
        thickness_mm: толщина фасада, мм. Если None — берётся типовая
            толщина FACADE_DEFAULT_THICKNESS_MM по материалу (или плотность
            не определена → вес неизвестен).
        density_kg_m3: плотность материала, кг/м³. Если None — берётся из
            FACADE_MATERIAL_DENSITY_KG_M3 по ключу material.
        material: материал фасада ("ldsp" | "mdf" | "glass" или название,
            см. _facade_material_key). Необязателен, если density_kg_m3 задан.

    Returns:
        вес фасада в кг или None, если данных недостаточно (вес неизвестен →
        вызывающий код использует чисто высотную таблицу).
    """
    if width_mm is None or height_mm is None:
        return None
    if width_mm <= 0 or height_mm <= 0:
        return None

    if density_kg_m3 is None:
        mat_key = _facade_material_key(material)
        if mat_key is None:
            return None
        density_kg_m3 = FACADE_MATERIAL_DENSITY_KG_M3[mat_key]
        if thickness_mm is None:
            thickness_mm = FACADE_DEFAULT_THICKNESS_MM.get(mat_key)
    if density_kg_m3 is None or density_kg_m3 <= 0:
        return None
    if thickness_mm is None or thickness_mm <= 0:
        return None

    volume_m3 = (width_mm / 1000) * (height_mm / 1000) * (thickness_mm / 1000)
    return round(volume_m3 * density_kg_m3, 2)


def calculate_hinges_for_modules(
    modules: List[Dict],
    hinge_price: float = 0,
    brand: str = DEFAULT_HINGE_BRAND
) -> Dict:
    """
    Расчёт петель для модулей с фасадами.

    Правила по высоте ОДНОЙ двери (см. hinges_per_door):
    - FIRMAX: ≥2000 → 4; 901-1999 → 3; ≤900 → 2
    - BLUM/HETTICH: >1600 → 5; 1200-1600 → 4; 900-1200 → 3; <900 → 2
      (+1 петля для «тяжёлых» фасадов, когда вес известен)

    Вес фасада оценивается из габаритов фасада и его материала, ЕСЛИ они
    доступны в фасаде модуля:
        facade = {"width_mm": 597, "height_mm": 816,
                  "material": "mdf", "thickness_mm": 18}
    Если материал/толщина фасада не заданы — hinges_per_door зовётся как
    раньше, без веса (чисто высотная таблица).

    Returns:
        {"total_hinges": кол-во, "cost": стоимость}
    """
    total_hinges = 0
    brand_key = (brand or "").upper()
    use_weight = brand_key in ("BLUM", "HETTICH")

    for module in modules:
        # Проверяем, есть ли фасады в модуле
        if "facades" not in module or not module["facades"]:
            continue

        for facade in module["facades"]:
            height_mm = facade.get("height_mm", 0)

            # Вес фасада: только для BLUM/HETTICH и только если фасад описан
            # материалом (или явной плотностью) + есть ширина для оценки.
            door_weight_kg = None
            if use_weight and facade.get("width_mm"):
                material = facade.get("material") or facade.get("type")
                door_weight_kg = estimate_facade_weight_kg(
                    width_mm=facade.get("width_mm"),
                    height_mm=height_mm,
                    thickness_mm=facade.get("thickness_mm"),
                    material=material,
                )

            total_hinges += hinges_per_door(
                height_mm, brand_key, door_weight_kg
            )

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
    drawer_prices: Optional[Dict[str, float]] = None,
    hinge_brand: str = DEFAULT_HINGE_BRAND
) -> HardwareCalculation:
    """
    Полный расчёт фурнитуры для модулей.
    """
    result = HardwareCalculation()

    # Петли
    hinges_data = calculate_hinges_for_modules(modules, hinge_price, brand=hinge_brand)
    result.hinges_total = hinges_data["total_hinges"]
    result.breakdown["hinges"] = hinges_data

    # Ящики
    drawers_data = calculate_drawers_for_modules(modules, drawer_prices)
    result.drawers_total = drawers_data["total_drawers"]
    result.breakdown["drawers"] = drawers_data

    # Общая стоимость
    result.total_cost = round(hinges_data["cost"] + drawers_data["cost"], 2)

    return result


# ═══════════════════════════════════════════════════════════════════
# ПОДЪЁМНИКИ ДЛЯ ВЕРХНИХ БАЗ
# ═══════════════════════════════════════════════════════════════════

# Цены подъёмников (₽/шт)
LIFT_PRICES = {
    "blum_hf": 14000,        # BLUM HF складной (премиум)
    "blum_hk_xs": 3500,      # BLUM HK-XS поворотный (средний)
    "dtc_top_stay": 2500,    # DTC Top Stay (эконом)
}

LIFT_NAMES = {
    "blum_hf": "BLUM HF складной подъёмник",
    "blum_hk_xs": "BLUM HK-XS поворотный подъёмник",
    "dtc_top_stay": "DTC Top Stay подъёмник",
}


def calculate_lifts(
    upper_module_count: int,
    lift_type: str = "dtc_top_stay",
    has_glass_facades: bool = False,
) -> Dict:
    """
    Рассчитать подъёмники для верхних баз.

    Правила:
    - По 1 подъёмнику на каждую верхнюю базу с горизонтальным открытием
    - Если фасады стеклянные/тяжёлые → премиум (BLUM HF)
    - Если фасады МДФ → средний (BLUM HK-XS)
    - Если фасады ЛДСП → эконом (DTC Top Stay)

    Args:
        upper_module_count: количество верхних баз
        lift_type: "blum_hf" | "blum_hk_xs" | "dtc_top_stay"
        has_glass_facades: есть ли стеклянные фасады
    
    Returns:
        {"count": шт, "type": тип, "name": название, "price_per": цена, "total_cost": стоимость}
    """
    # Автовыбор типа подъёмника
    if lift_type == "auto":
        if has_glass_facades:
            lift_type = "blum_hf"
        else:
            lift_type = "dtc_top_stay"  # эконом по умолчанию
    
    price_per = LIFT_PRICES.get(lift_type, LIFT_PRICES["dtc_top_stay"])
    name = LIFT_NAMES.get(lift_type, LIFT_NAMES["dtc_top_stay"])
    
    return {
        "count": upper_module_count,
        "type": lift_type,
        "name": name,
        "price_per": price_per,
        "total_cost": round(upper_module_count * price_per, 2),
    }


# ═══════════════════════════════════════════════════════════════════
# ШТАНГИ ДЛЯ ГАРДЕРОБНЫХ / ПЕНАЛОВ
# ═══════════════════════════════════════════════════════════════════

ROD_PRICES = {
    "round_d25": 407,              # Штанга круглая D25 (₽/шт)
    "round_holder": 17,            # Держатель круглой штанги (₽/шт)
    "rectangular_3000": 1700,      # Штанга прямоугольная 3000мм (₽/шт)
    "rectangular_holder": 180,     # Держатель прямоугольной штанги (₽/шт)
}


def calculate_rods(
    modules: List[Dict],
) -> Dict:
    """
    Рассчитать штанги для гардеробных и пеналов.

    Правила:
    - Пенал с шириной ≥600мм → 1 прямоугольная штанга
    - Каждая штанга = 1 шт. + 2 держателя
    - Нижняя/верхняя база ≥600мм в гардеробной → 1 круглая штанга

    Returns:
        {
            "round_count": шт,
            "round_holders": шт,
            "rectangular_count": шт,
            "rectangular_holders": шт,
            "total_cost": стоимость
        }
    """
    round_count = 0
    rectangular_count = 0
    
    for m in modules:
        module_type = m.get("type", "")
        width = m.get("width", 0)
        qty = max(m.get("quantity", 1), 1)
        
        if module_type == "penal" and width >= 600:
            rectangular_count += 1 * qty
        elif module_type in ("lower_base", "upper_base") and width >= 600:
            round_count += 1 * qty
    
    result = {
        "round_count": round_count,
        "round_holders": round_count * 2,
        "rectangular_count": rectangular_count,
        "rectangular_holders": rectangular_count * 2,
    }
    
    result["total_cost"] = round(
        round_count * ROD_PRICES["round_d25"] +
        result["round_holders"] * ROD_PRICES["round_holder"] +
        rectangular_count * ROD_PRICES["rectangular_3000"] +
        result["rectangular_holders"] * ROD_PRICES["rectangular_holder"],
        2
    )
    
    return result