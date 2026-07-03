"""
Калькулятор количества материалов на основе AI-модулей.

Переводит модули (600×560×820) в реальные позиции прайса:
- Листы ЛДСП (площадь деталей / площадь листа × k запаса на раскрой)
- Кромка (ВСЕ торцы всех деталей — кромим вкруг)
- Фасады (площадь фасадов × цена за м²)
- Петли (высота фасада → N петель с запасом)
- Ящики (ширина фасада → тип системы; рекомендации)
- Gola (горизонтальные + вертикальные профиля)
- LED, комплектующие, эргономика
"""

import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from app.services.full_pipeline import PipelineResult, RoomSpec
from app.services.image_analyzer import RecognizedModule


# Стандартные размеры листов
SHEET_LDSP = {"width_mm": 2800, "height_mm": 2070, "area_m2": 5.796}
SHEET_MDF  = {"width_mm": 2800, "height_mm": 1220, "area_m2": 3.416}

# Коэффициенты запаса на раскрой (увеличены — реальный раскрой)
WASTE_FACTOR_PLAIN   = 1.35   # однотонный ЛДСП (деловой отход ~25%)
WASTE_FACTOR_TEXTURE = 1.40   # текстурный ЛДСП (учёт направления рисунка)
WASTE_FACTOR_MDF     = 1.25   # МДФ фасады

# Толщина кромки по умолчанию (кромим ВСЕ детали вкруг)
EDGE_DEFAULT_MM  = 0.4   # невидимые торцы
EDGE_VISIBLE_MM  = 0.8   # видимые (фасадные) торцы
EDGE_PREMIUM_MM  = 2.0   # премиум (влагостойкие зоны)

# Запас кромки на обработку
EDGE_OVERLAP     = 1.05  # +5%


def detect_material_properties(materials: List[str]) -> dict:
    """
    Единая точка определения свойств материала по AI-распознанным названиям.
    
    Используется ВСЕМИ модулями: quantity_calc, template_filler, calculation_excel.
    Изменения в логике детекции делаются ТОЛЬКО здесь.
    
    Returns:
        {
            "surface": "plain" | "texture",
            "brand": "EGGER" | "EXTRAVERT" | "LAMARTY" | "ТОМЛЕСДРЕВ" | "unknown",
            "facade_type": "pvh" | "emdiway" | "emdiway_titan" | "paint_matte" | "paint_gloss" | "unknown",
            "has_glass": bool,
        }
    """
    result = {"surface": "plain", "brand": "unknown", "facade_type": "unknown", "has_glass": False}
    
    if not materials:
        return result
    
    materials_upper = " ".join(m.upper() for m in materials)
    
    # ── Бренд ЛДСП ──
    for brand in ["EGGER", "EXTRAVERT", "LAMARTY"]:
        if brand in materials_upper:
            result["brand"] = brand
            break
    if "ТОМЛЕСДРЕВ" in materials_upper:
        result["brand"] = "ТОМЛЕСДРЕВ"
    
    # ── Текстура vs однотон ──
    TEXTURE_KEYWORDS = ["ТЕКСТУР", "ДРЕВЕСН", "WOOD", "ДУБ", "ОРЕХ", "ЯСЕНЬ"]
    if any(kw in materials_upper for kw in TEXTURE_KEYWORDS):
        result["surface"] = "texture"
    elif any(m.upper().startswith(p) for m in materials for p in ["H1", "H3"]):
        # H1xxx, H3xxx — древесные декоры EGGER
        result["surface"] = "texture"
    
    # ── Тип фасада ──
    if "EMDIWAY" in materials_upper:
        result["facade_type"] = "emdiway_titan" if "TITAN" in materials_upper else "emdiway"
    elif any(kw in materials_upper for kw in ["ЛАКОКРАСКА", "МАТОВЫЙ", "МАТОВАЯ"]):
        result["facade_type"] = "paint_matte"
    elif "ГЛЯНЕЦ" in materials_upper or "ГЛЯНЦЕВ" in materials_upper:
        result["facade_type"] = "paint_gloss"
    elif "ПВХ" in materials_upper:
        result["facade_type"] = "pvh"
    
    # ── Стекло ──
    result["has_glass"] = any(
        kw in materials_upper for kw in ["СТЕКЛО", "ЗЕРКАЛО", "GLASS", "MIRROR"]
    )
    
    return result


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

    # Кромка — ВСЕ торцы (детали кромятся вкруг)
    edge_total_m: float = 0      # общий метраж кромки (0.4мм)
    edge_visible_m: float = 0    # видимые торцы (0.8мм)
    edge_premium_m: float = 0    # премиум (2мм)
    edge_04_m: float = 0         # итого 0.4мм (невидимые = общие − видимые − премиум)
    edge_08_m: float = 0         # итого 0.8мм
    edge_2_m: float = 0          # итого 2мм

    # Фасады
    facades_area_m2: float = 0
    facade_type: Optional[str] = None  # "ПВХ IVEGO" / "EMDIWAY" / "Лакокраска"

    # Фурнитура
    hinges_count: int = 0
    hinge_brand: str = "FIRMAX"   # FIRMAX / BLUM / HETTICH

    drawers_count: int = 0
    drawer_system: str = "Tandembox"  # Tandembox / Legrabox / Boyard Start
    drawers_internal_count: int = 0   # внутренние ящики (для столовых приборов)

    # Gola — вертикальные и горизонтальные
    gola_vertical_m: float = 0     # вертикальные профиля (межмодульные)
    gola_horizontal_m: float = 0   # горизонтальные профиля (столешница)
    gola_vertical_pcs: int = 0     # штук по 3м
    gola_horizontal_pcs: int = 0   # штук по 3м

    # LED
    led_strip_m: float = 0
    led_power_supply: int = 0
    led_sensor: int = 0

    # Комплектующие
    has_sink: bool = False
    bottle_holder_count: int = 0
    cutlery_tray_count: int = 0
    drying_rack_count: int = 0

    # Эргономика / рекомендации
    suggestions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def calculate_quantities(modules: List[RecognizedModule], room_name: str = "", materials: List[str] = None, *, auto_accessories: bool = True) -> MaterialQuantities:
    """
    Рассчитать количества материалов для списка модулей.

    Учтены реальные нормы:
    - Кромка на ВСЕ торцы (детали кромятся вкруг)
    - Запас на раскрой ЛДСП ~35-40%
    - Петли стандартные (2-5 на фасад)
    - Gola вертикальные + горизонтальные
    - Эргономические рекомендации

    Args:
        modules: список AI-распознанных модулей
        room_name: название помещения (для эвристик)
        materials: список AI-распознанных материалов
        auto_accessories: автоматически добавлять сушки/лотки/ящики (False = только расчёт)
    """
    q = MaterialQuantities()
    materials = materials or []

    room_lower = room_name.lower()
    is_kitchen = any(kw in room_lower for kw in ["кухн", "остров", "гарнитур", "kitchen"])
    is_wardrobe = any(kw in room_lower for kw in ["гардероб", "шкаф", "wardrobe", "прием", "приём"])

    # Определяем свойства материала (единая функция)
    mat_props = detect_material_properties(materials)
    q.ldsp_material = "текстура" if mat_props["surface"] == "texture" else "однотон"

    total_hdf_area = 0.0  # Суммируем площадь, делим в конце
    total_back_area = 0.0
    lower_modules_width = 0.0  # Суммарная ширина нижних баз (для Gola)
    lower_modules_count = 0
    visible_module_count = 0  # Количество «видимых» модулей (для верт. Gola)

    for module in modules:
        w_m = module.width / 1000
        d_m = module.depth / 1000
        h_m = module.height / 1000
        qty = max(module.quantity, 1)

        # ── Детали корпуса (на 1 модуль) ──
        sides_area      = 2 * (d_m * h_m)        # 2 боковины
        bottom_top_area = 2 * (w_m * d_m)         # дно + крыша
        back_area       = w_m * h_m               # задняя стенка (ХДФ)
        shelf_area      = w_m * d_m * max(module.shelves, 1 if module.type in ("lower_base", "upper_base") else 0)

        ldsp_per_module = sides_area + bottom_top_area + shelf_area
        q.ldsp_area_m2 += ldsp_per_module * qty

        # ХДФ — суммируем площадь, пересчитаем в листы в конце
        total_hdf_area += back_area * qty

        # ── КРОМКА: ВСЕ торцы ВСЕХ деталей (кромим вкруг) ──
        # Каждая деталь имеет 4 стороны. Считаем периметр каждой детали.
        # 2 боковины: 4 стороны каждая = 2*(d+h) × 2
        # Дно + крыша: 4 стороны каждая = 2*(w+d) × 2
        # Полки: 4 стороны каждая = 2*(w+d) × N
        # ХДФ (задняя стенка): обычно не кромится или кромится 0.4мм — считаем

        sides_perimeter      = 2 * (2 * (d_m + h_m))   # периметр 2 боковин
        bottom_top_perimeter = 2 * (2 * (w_m + d_m))   # периметр дна + крыши
        shelf_perimeter      = max(module.shelves, 1 if module.type in ("lower_base", "upper_base") else 0) * (2 * (w_m + d_m))
        back_perimeter       = 2 * (w_m + h_m)          # задняя стенка (кромится 0.4)

        total_perimeter = (sides_perimeter + bottom_top_perimeter + shelf_perimeter + back_perimeter) * qty

        # Распределение по типу кромки:
        # - Видимые (передние) торцы: фасадная сторона боковин + дно + крыша
        #   = 2*(h_m) для боковин + 2*(w_m) для дна/крыши
        # - Невидимые: всё остальное → 0.4мм
        # - Премиум (2мм): если есть мойка или влажная зона

        visible_per_module = (2 * h_m + 2 * w_m)  # передние кромки
        visible_total = visible_per_module * qty

        # Премиум 2мм — не используется (по требованию заказчика)
        premium_total = 0

        q.edge_total_m    += total_perimeter * EDGE_OVERLAP
        q.edge_visible_m  += visible_total * EDGE_OVERLAP
        q.edge_premium_m  += premium_total * EDGE_OVERLAP

        # ── Фасады ──
        if module.facades and module.facades.get("count", 0) > 0:
            facade_count = module.facades.get("count", 1)
            facade_h_m = (module.height - 4) / 1000   # зазор 4мм
            facade_w_m = (module.width / facade_count - 3) / 1000  # зазор 3мм
            q.facades_area_m2 += facade_w_m * facade_h_m * facade_count * qty

        # ── Петли (стандартная формула) ──
        if module.type in ("lower_base", "upper_base", "penal", "column", "tumbler"):
            facade_h = module.height
            if facade_h < 900:
                hinges = 2
            elif facade_h <= 1400:
                hinges = 3
            elif facade_h <= 1900:
                hinges = 4
            elif facade_h <= 2400:
                hinges = 5
            else:
                hinges = 6

            if module.facades:
                facade_count = module.facades.get("count", 1)
                q.hinges_count += hinges * facade_count * qty

        # ── Ящики (AI + рекомендации) ──
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

        # Счётчики для Gola
        if module.type == "lower_base":
            lower_modules_width += (module.width / 1000) * qty
            lower_modules_count += qty
        if module.type in ("lower_base", "upper_base", "penal"):
            visible_module_count += qty

    # ── ИТОГОВЫЕ РАСЧЁТЫ ──

    # ЛДСП: листы с запасом на раскрой
    waste = WASTE_FACTOR_TEXTURE if "текстур" in (q.ldsp_material or "").lower() else WASTE_FACTOR_PLAIN
    q.ldsp_sheets = max(1, math.ceil(q.ldsp_area_m2 * waste / SHEET_LDSP["area_m2"])) if q.ldsp_area_m2 > 0 else 0
    q.mdf_sheets  = max(0, math.ceil(q.facades_area_m2 * WASTE_FACTOR_MDF / SHEET_MDF["area_m2"]))

    # Учёт смежных стенок: соседние модули с одинаковой глубиной делят боковину
    # Экономия ~0.011 м² ЛДСП на каждый стык (560мм глубина × ~20мм толщина)
    SHARED_SIDE_SAVINGS_M2 = 0.0112
    modules_by_depth: dict = {}
    for m in modules:
        modules_by_depth.setdefault((m.type, m.depth), []).append(m)
    shared_pairs = 0
    for mods in modules_by_depth.values():
        shared_pairs += max(0, sum(m.quantity for m in mods) - 1)
    if shared_pairs > 0:
        savings = shared_pairs * SHARED_SIDE_SAVINGS_M2
        q.ldsp_area_m2 = max(0, q.ldsp_area_m2 - savings)
        # Пересчитываем листы с учётом экономии
        q.ldsp_sheets = max(1, math.ceil(q.ldsp_area_m2 * waste / SHEET_LDSP["area_m2"])) if q.ldsp_area_m2 > 0.1 else q.ldsp_sheets
        logger.info(f"Учтено {shared_pairs} смежных стенок, экономия {savings:.2f} м² ЛДСП → листов: {q.ldsp_sheets}")

    # ХДФ: для кухонь — минус 40% (техника, мойка без задней стенки)
    if total_hdf_area > 0:
        if is_kitchen:
            total_hdf_area *= 0.6  # ~40% модулей без ХДФ (техника, мойка)
        q.hdf_sheets = max(1, math.ceil(total_hdf_area * WASTE_FACTOR_PLAIN / SHEET_MDF["area_m2"]))

    # Кромка: распределяем
    # edge_total_m — всё
    # edge_premium_m — 2мм
    # edge_visible_m — 0.8мм (включая премиум)
    # Остальное → 0.4мм
    q.edge_2_m  = math.ceil(q.edge_premium_m)
    q.edge_08_m = math.ceil(q.edge_visible_m)
    q.edge_04_m = math.ceil(q.edge_total_m - q.edge_visible_m - q.edge_premium_m)
    if q.edge_04_m < 0:
        q.edge_04_m = 0

    # ── Gola: вертикальные + горизонтальные ──
    if is_kitchen:
        # Горизонтальный Gola: сумма ширин нижних баз
        q.gola_horizontal_m = lower_modules_width

        # Вертикальный Gola: 2 шт (по одному на каждый открытый торец)
        if visible_module_count > 0 and modules:
            avg_height_m = sum((m.height / 1000) * max(m.quantity, 1) for m in modules
                               if m.type in ("lower_base", "upper_base", "penal")) / max(visible_module_count, 1)
            q.gola_vertical_m = avg_height_m * 2  # 2 открытых торца
            q.gola_vertical_pcs = 2  # всегда 2 штуки (левый + правый торец)

        # Штуки по 3 метра
        q.gola_horizontal_pcs = math.ceil(q.gola_horizontal_m / 3) if q.gola_horizontal_m > 0 else 0

        # LED: 70% от длины горизонтального Gola
        q.led_strip_m      = q.gola_horizontal_m * 0.7
        q.led_power_supply = max(1, math.ceil(q.led_strip_m / 10)) if q.led_strip_m > 0 else 0
        q.led_sensor       = 1 if q.led_strip_m > 0 else 0

    # ── Эргономика / рекомендации (только если включены) ──
    if auto_accessories:
        if is_kitchen:
            # Ящики: если AI не нашёл — рекомендуем
            if q.drawers_count == 0:
                q.drawers_count = 2           # минимум 2 ящика на кухню
                q.drawers_internal_count = 1   # один внутренний для приборов
                q.drawer_system = "Tandembox"
                q.suggestions.append(
                    "🍴 Рекомендация: добавить 2 ящика Tandembox — "
                    "один стандартный, один с внутренним для столовых приборов"
                )

            # Лоток для приборов
            q.cutlery_tray_count = 1
            # Бутылочница: если есть узкий модуль (≤200мм)
            q.bottle_holder_count = 1 if any(m.width <= 200 for m in modules) else 0
            # Мойка — предполагаем что есть
            q.has_sink = True
            if q.has_sink:
                q.drying_rack_count = 1
                q.suggestions.append(
                    "💧 Рекомендация: сушка для посуды Alba в модуль 900мм"
                )

            # Зоны хранения
            has_upper = any(m.type == "upper_base" for m in modules)
            has_lower = any(m.type == "lower_base" for m in modules)
            if has_upper:
                q.suggestions.append(
                    "📦 Верхние базы: посуда, чашки, специи, лёгкие продукты"
                )
            if has_lower:
                q.suggestions.append(
                    "📦 Нижние базы: кастрюли, сковородки, бытовая химия, мойка"
                )

        if is_wardrobe:
            q.suggestions.append(
                "👔 Рекомендация: штанга для одежды + полки для обуви в нижней зоне"
            )
            # Штанга
            for m in modules:
                if m.type == "penal" and m.width >= 600:
                    q.suggestions.append(
                        f"👕 Штанга прямоугольная в пенал {m.width}мм — {m.quantity} шт."
                    )

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
    q = calculate_quantities(modules, room_name, materials)
    items = []

    room_lower = room_name.lower()
    is_kitchen = any(kw in room_lower for kw in ["кухн", "остров", "гарнитур", "kitchen"])

    # Определяем материал через единую функцию
    mat_props = detect_material_properties(materials)
    is_texture = mat_props["surface"] == "texture"
    ldsp_material = "EGGER текстура" if is_texture else "EGGER однотон"
    ldsp_price = 6000

    # ── 1. ЛДСП ──
    if q.ldsp_sheets > 0:
        items.append(("ЛДСП", f"EGGER ЛДСП {ldsp_material}", f"{q.ldsp_sheets} листов × {ldsp_price}₽",
                      ldsp_price, q.ldsp_sheets))

    # ── 2. Кромка ──
    if q.edge_08_m > 0:
        items.append(("КРОМКА", "EGGER 0,8*19", f"{q.edge_08_m:.0f} м.п. × 34₽",
                      34, q.edge_08_m))
    if q.edge_04_m > 10:  # только если значимое количество
        items.append(("КРОМКА", "EGGER 0,4*19", f"{q.edge_04_m:.0f} м.п. × 17.5₽",
                      17.5, q.edge_04_m))
    if q.edge_2_m > 0:
        items.append(("КРОМКА", "EGGER 2*19", f"{q.edge_2_m:.0f} м.п. × 60₽",
                      60, q.edge_2_m))

    # ── 3. ХДФ ──
    if q.hdf_sheets > 0:
        items.append(("ХДФ", "ЛХДФ (задние стенки)", f"{q.hdf_sheets} листов × 1530₽",
                      1530, q.hdf_sheets))

    # ── 4. МДФ / Фасады ──
    if q.facades_area_m2 > 0:
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
    if q.drawers_internal_count > 0:
        items.append(("ЯЩИКИ", f"Ящик внутренний {q.drawer_system}",
                      f"{q.drawers_internal_count} шт × 9000₽",
                      9000, q.drawers_internal_count))

    # ── 7. Gola ──
    if q.gola_horizontal_pcs > 0:
        items.append(("GOLA", "GOLA профиль горизонтальный 3 м (C,L) черный",
                      f"{q.gola_horizontal_pcs} шт × 3550₽", 3550, q.gola_horizontal_pcs))
    if q.gola_vertical_pcs > 0:
        items.append(("GOLA", "GOLA профиль вертикальный боковой 3 м",
                      f"{q.gola_vertical_pcs} шт × 1835₽", 1835, q.gola_vertical_pcs))

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
