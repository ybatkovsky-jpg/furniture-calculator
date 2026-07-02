"""
Калькулятор количества материалов на основе AI-модулей.

Переводит модули (600×560×820) в реальные позиции прайса:
- Листы ЛДСП (площадь деталей / площадь листа × k запаса)
- Кромка (периметр деталей × тип кромки)
- Фасады (площадь фасадов × цена за м²)
- Петли (высота фасада → N петель)
- Ящики (ширина фасада → тип системы)
- Gola, LED, комплектующие
"""

import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from app.services.full_pipeline import PipelineResult, RoomSpec
from app.services.image_analyzer import RecognizedModule


# Стандартные размеры листов
SHEET_LDSP = {"width_mm": 2800, "height_mm": 2070, "area_m2": 5.796}
SHEET_MDF = {"width_mm": 2800, "height_mm": 1220, "area_m2": 3.416}
WASTE_FACTOR_PLAIN = 1.15    # однотонный ЛДСП
WASTE_FACTOR_TEXTURE = 1.20  # текстурный ЛДСП
EDGE_OVERLAP_MM = 2          # нахлёст кромки


@dataclass
class MaterialQuantities:
    """Рассчитанные количества материалов."""
    # ЛДСП
    ldsp_area_m2: float = 0
    ldsp_sheets: int = 0
    ldsp_material: Optional[str] = None  # "EGGER однотон" / "EGGER текстура"

    # МДФ (фасады)
    mdf_area_m2: float = 0
    mdf_sheets: int = 0
    mdf_material: Optional[str] = None

    # ХДФ (задние стенки)
    hdf_sheets: int = 0

    # Кромка
    edge_04_m: float = 0   # 0.4 мм (скрытые торцы)
    edge_08_m: float = 0   # 0.8 мм (видимые торцы)
    edge_2_m: float = 0    # 2 мм (премиум)

    # Фасады
    facades_area_m2: float = 0
    facade_type: Optional[str] = None  # "ПВХ IVEGO" / "EMDIWAY" / "Лакокраска"

    # Фурнитура
    hinges_count: int = 0
    hinge_brand: str = "FIRMAX"   # FIRMAX / BLUM / HETTICH
    drawers_count: int = 0
    drawer_system: str = "Tandembox"  # Tandembox / Legrabox / Boyard Start

    # Gola
    gola_vertical_m: float = 0
    gola_horizontal_m: float = 0
    gola_with_led: bool = False

    # LED
    led_strip_m: float = 0
    led_power_supply: int = 0
    led_sensor: int = 0

    # Комплектующие
    has_sink: bool = False
    bottle_holder_count: int = 0
    cutlery_tray_count: int = 0
    drying_rack_count: int = 0

    # Примечания
    warnings: List[str] = field(default_factory=list)


def calculate_quantities(modules: List[RecognizedModule], room_name: str = "") -> MaterialQuantities:
    """
    Рассчитать количества материалов для списка модулей.

    Args:
        modules: список AI-распознанных модулей
        room_name: название помещения (для эвристик)
    """
    q = MaterialQuantities()

    for module in modules:
        w_m = module.width / 1000
        d_m = module.depth / 1000
        h_m = module.height / 1000
        qty = max(module.quantity, 1)

        # ── ЛДСП (корпус) ──
        # Детали: 2 боковины + дно + крыша + задняя стенка (ХДФ) + полки
        # Площадь на 1 модуль:
        sides = 2 * (d_m * h_m)           # 2 боковины
        bottom_top = 2 * (w_m * d_m)       # дно + крыша
        back = w_m * h_m                   # задняя стенка (ХДФ)
        shelves = w_m * d_m                # 1 полка (если shelves > 0)

        ldsp_per_module = sides + bottom_top + (shelves * max(module.shelves, 0))
        q.ldsp_area_m2 += ldsp_per_module * qty

        # ХДФ
        q.hdf_sheets += math.ceil((back * qty) / SHEET_MDF["area_m2"])

        # ── Кромка ──
        # Видимые торцы: передние кромки боковин + дно + крыша
        visible_edge = (2 * h_m + 2 * w_m) * qty  # периметр фасада
        hidden_edge = (2 * h_m + 2 * d_m) * qty   # торцы боковин внутри

        q.edge_08_m += visible_edge * 1.05    # +5% запас
        q.edge_04_m += hidden_edge * 1.05

        # ── Фасады ──
        if module.facades and module.facades.get("count", 0) > 0:
            facade_count = module.facades.get("count", 1)
            facade_h_m = (module.height - 4) / 1000  # зазор 4мм
            facade_w_m = (module.width / facade_count - 3) / 1000  # зазор 3мм
            q.facades_area_m2 += facade_w_m * facade_h_m * facade_count * qty

        # ── Петли ──
        if module.type in ("lower_base", "upper_base", "penal", "column", "tumbler"):
            facade_h = module.height
            if facade_h < 900:
                hinges = 2
            elif facade_h <= 1400:
                hinges = 3
            elif facade_h <= 1900:
                hinges = 4
            else:
                hinges = 5

            if module.facades:
                facade_count = module.facades.get("count", 1)
                q.hinges_count += hinges * facade_count * qty

        # ── Ящики ──
        if module.drawers and module.drawers.get("count", 0) > 0:
            drawer_count = module.drawers.get("count", 1)
            q.drawers_count += drawer_count * qty

            # Определяем систему ящиков по ширине фасада
            facade_w = module.width
            if facade_w > 900:
                q.drawer_system = "Legrabox"
            elif facade_w > 600:
                q.drawer_system = "Tandembox"
            else:
                q.drawer_system = "Boyard Start"

    # ── Округление листов ──
    waste = WASTE_FACTOR_TEXTURE if "текстур" in (q.ldsp_material or "").lower() else WASTE_FACTOR_PLAIN
    q.ldsp_sheets = max(1, math.ceil(q.ldsp_area_m2 * waste / SHEET_LDSP["area_m2"]))
    q.mdf_sheets = max(0, math.ceil(q.facades_area_m2 * WASTE_FACTOR_PLAIN / SHEET_MDF["area_m2"]))

    # ── Gola (только для кухни) ──
    room_lower = room_name.lower()
    if any(kw in room_lower for kw in ["кухн", "остров", "гарнитур"]):
        # Горизонтальный Gola: длина всех нижних баз
        for m in modules:
            if m.type == "lower_base":
                q.gola_horizontal_m += (m.width / 1000) * m.quantity
        q.gola_with_led = True
        q.led_strip_m = q.gola_horizontal_m * 0.7  # LED на 70% длины
        q.led_power_supply = 1
        q.led_sensor = 1

    # ── Комплектующие ──
    if any(kw in room_lower for kw in ["кухн", "гарнитур"]):
        q.cutlery_tray_count = 1  # лоток для приборов — всегда на кухне
        q.bottle_holder_count = 1 if any(m.width <= 200 for m in modules) else 0
        q.has_sink = True  # предполагаем что мойка есть
        if q.has_sink:
            q.drying_rack_count = 1

    return q


def fill_template_for_room(
    modules: List[RecognizedModule],
    room_name: str,
    materials: List[str],
) -> List[Tuple[str, str, str, float, float]]:
    """
    Заполнить позиции шаблона для одного помещения.

    Returns:
        Список кортежей: (категория, наименование, цвет/поставщик, цена, количество)
    """
    q = calculate_quantities(modules, room_name)
    items = []

    # Определяем материал ЛДСП из AI-распознанных
    ldsp_material = "EGGER однотон"
    ldsp_price = 6000
    for mat in materials:
        mat_upper = mat.upper()
        if "ТЕКСТУР" in mat_upper or "H1" in mat_upper:
            ldsp_material = "EGGER текстура"
            ldsp_price = 6650
            q.ldsp_material = "текстура"
            break

    # ── 1. ЛДСП ──
    if q.ldsp_sheets > 0:
        items.append(("ЛДСП", f"EGGER ЛДСП {ldsp_material}", f"{q.ldsp_sheets} листов × {ldsp_price}₽",
                      ldsp_price, q.ldsp_sheets))

    # ── 2. Кромка ──
    if q.edge_08_m > 0:
        items.append(("КРОМКА", "EGGER 0,8*19", f"{q.edge_08_m:.0f} м.п. × 34₽",
                      34, math.ceil(q.edge_08_m)))
    if q.edge_04_m > 10:  # только если значимое количество
        items.append(("КРОМКА", "EGGER 0,4*19", f"{q.edge_04_m:.0f} м.п. × 17.5₽",
                      17.5, math.ceil(q.edge_04_m)))

    # ── 3. ХДФ ──
    if q.hdf_sheets > 0:
        items.append(("ХДФ", "ЛХДФ (задние стенки)", f"{q.hdf_sheets} листов × 1530₽",
                      1530, q.hdf_sheets))

    # ── 4. МДФ / Фасады ──
    if q.facades_area_m2 > 0:
        # Определяем тип фасада
        facade_price = 5729  # ПВХ I категория
        facade_name = "ФАСАДЫ ПВХ 16мм"
        for mat in materials:
            mat_upper = mat.upper()
            if "EMDIWAY" in mat_upper:
                facade_price = 9800
                facade_name = "EMDIWAY однотонный"
                break
            elif "ЛАКОКРАСКА" in mat_upper or "МАТОВ" in mat_upper:
                facade_price = 10450
                facade_name = "Лакокраска матовая"
                break

        items.append(("ФАСАДЫ", facade_name,
                      f"{q.facades_area_m2:.1f} м² × {facade_price}₽",
                      facade_price, round(q.facades_area_m2, 1)))

    # ── 5. Петли ──
    if q.hinges_count > 0:
        hinge_price = 200  # FIRMAX с доводчиком
        items.append(("ПЕТЛИ", f"Петля {q.hinge_brand} с доводчиком",
                      f"{q.hinges_count} шт × {hinge_price}₽",
                      hinge_price, q.hinges_count))

    # ── 6. Ящики ──
    if q.drawers_count > 0:
        drawer_prices = {"Legrabox": 7230, "Tandembox": 5300, "Boyard Start": 1656}
        dp = drawer_prices.get(q.drawer_system, 5300)
        items.append(("ЯЩИКИ", f"Ящик {q.drawer_system}",
                      f"{q.drawers_count} шт × {dp}₽",
                      dp, q.drawers_count))

    # ── 7. Gola ──
    if q.gola_horizontal_m > 0:
        gola_m = math.ceil(q.gola_horizontal_m / 3) * 3  # кратно 3м
        items.append(("GOLA", "GOLA профиль горизонтальный 3 м (C,L) черный",
                      f"{gola_m / 3:.0f} шт × 3550₽", 3550, gola_m / 3))

    # ── 8. LED ──
    if q.led_strip_m > 0:
        items.append(("ПОДСВЕТКА", "Подсветка LED (Бухта 5м)", "", 1820, 1))
        items.append(("ПОДСВЕТКА", "Блок питания", "", 1171, q.led_power_supply))
        items.append(("ПОДСВЕТКА", "Датчик", "", 1555, q.led_sensor))

    # ── 9. Комплектующие ──
    if q.cutlery_tray_count > 0:
        items.append(("КОМПЛЕКТУЮЩИЕ", "Лоток для столовых приборов", "", 2500, q.cutlery_tray_count))
    if q.bottle_holder_count > 0:
        items.append(("БУТЫЛОЧНИЦЫ", "Выдвижная корзина 2-х уровн.(150) Flora BOYARD", "", 3000, q.bottle_holder_count))
    if q.drying_rack_count > 0:
        items.append(("СУШКИ", "Сушка для посуды Alba, в модуль на 900мм", "", 2500, q.drying_rack_count))

    return items
