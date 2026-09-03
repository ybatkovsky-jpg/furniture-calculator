"""
Калькулятор количества материалов на основе AI-модулей.

Переводит модули (600×560×820) в реальные позиции прайса:
- Листы ЛДСП (площадь деталей / площадь листа × k запаса на раскрой)
- Кромка (ВСЕ торцы всех деталей — кромим вкруг; 0 для МДФ в плёнке/краске)
- Фасады (площадь фасадов × цена за м²; плёнка ПВХ IVEGO)
- Петли (таблица: высота + вес фасада → N петель)
- Ручки (Gola → 0, push-to-open → 0, обычные ≤1200→1, >1200→2)
- Ящики (ширина фасада → тип системы; рекомендации)
- Gola (горизонтальные + вертикальные профиля)
- Столешница (длина + свес + подрезка)
- LED, крепёж, комплектующие, эргономика

Версия 2.0 — обновлено по промпту v4.0 (2026-07-11):
- Разные размеры листов по производителю
- Учёт пропила 4мм
- Кромка МДФ: плёнка/краска → 0, плитный МДФ → 1мм
- Запас на кромку 30%
- Петли по таблице высота×вес
- Ручки, плёнка ПВХ, столешница
"""

import math
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

from app.services.full_pipeline import PipelineResult, RoomSpec
from app.services.image_analyzer import RecognizedModule
from app.services.furniture_defaults import get_rules, FurnitureRules


# ═══════════════════════════════════════════════════════════════════
# РАЗМЕРЫ ЛИСТОВ ПО ПРОИЗВОДИТЕЛЮ
# ═══════════════════════════════════════════════════════════════════

# ЛДСП: разные производители — разные размеры листов
SHEET_LDSP_BY_BRAND = {
    "EGGER":     {"width_mm": 2750, "height_mm": 1830, "area_m2": 5.0325},
    "EXTRAVERT": {"width_mm": 2750, "height_mm": 1830, "area_m2": 5.0325},
    "LAMARTY":   {"width_mm": 2800, "height_mm": 2070, "area_m2": 5.796},
    "EMDIWAY":   {"width_mm": 2800, "height_mm": 2070, "area_m2": 5.796},
    "default":   {"width_mm": 2800, "height_mm": 2070, "area_m2": 5.796},
}

# МДФ: стандартный лист + большой формат
SHEET_MDF_STANDARD = {"width_mm": 2800, "height_mm": 1220, "area_m2": 3.416}
SHEET_MDF_LARGE    = {"width_mm": 3050, "height_mm": 2070, "area_mm2": 3050*2070, "area_m2": 6.3135}

# Толщина реза пилы (мм) — добавляется к каждому размеру детали
SAW_KERF_MM = 4

# Коэффициенты запаса на раскрой
WASTE_FACTOR_PLAIN   = 1.30   # однотонный ЛДСП (запас 30%)
WASTE_FACTOR_TEXTURE = 1.30   # текстурный ЛДСП (запас 30%, раскрой вдоль волокна)
WASTE_FACTOR_MDF     = 1.15   # МДФ фасады (запас 15% — точнее раскрой)
WASTE_FACTOR_HDF     = 1.30   # ХДФ задние стенки (запас 30%)
WASTE_FACTOR_PVC     = 1.10   # Плёнка ПВХ (запас 10%)

# Толщина кромки
EDGE_INVISIBLE_MM = 0.4   # невидимые торцы ЛДСП
EDGE_VISIBLE_MM   = 0.8   # видимые торцы ЛДСП
EDGE_PREMIUM_MM   = 2.0   # премиум (влагостойкие зоны, высокие фасады)
EDGE_MDF_MM       = 1.0   # кромка для плитного МДФ (EVOGLOSS, EMDIWAY, AGT и т.д.)

# Запас кромки на обработку (30% по промпту v4.0)
EDGE_OVERLAP = 1.30


@dataclass
class MaterialQuantities:
    """Рассчитанные количества материалов."""
    # ЛДСП
    ldsp_area_m2: float = 0
    ldsp_sheets: int = 0
    ldsp_material: Optional[str] = None  # "EGGER однотон" / "EGGER текстура"
    ldsp_brand: str = ""                 # "EGGER" / "EXTRAVERT" / "LAMARTY"

    # МДФ (фасады)
    mdf_area_m2: float = 0
    mdf_sheets: int = 0
    mdf_material: Optional[str] = None   # "EMDIWAY" / "EVOGLOSS" / "AGT" / etc.

    # Плёнка ПВХ (IVEGO)
    pvc_film_m2: float = 0              # площадь плёнки ПВХ для фасадов

    # ХДФ (задние стенки)
    hdf_sheets: int = 0

    # Кромка ЛДСП — ВСЕ торцы (детали кромятся вкруг)
    edge_total_m: float = 0      # общий метраж кромки ЛДСП
    edge_visible_m: float = 0    # видимые торцы (0.8мм)
    edge_premium_m: float = 0    # премиум (2мм)
    edge_04_m: float = 0         # итого 0.4мм (невидимые = общие − видимые − премиум)
    edge_08_m: float = 0         # итого 0.8мм
    edge_2_m: float = 0          # итого 2мм

    # Кромка МДФ (1мм — для плитного МДФ: EVOGLOSS, EMDIWAY, AGT и т.д.)
    edge_mdf_1mm_m: float = 0    # метраж кромки 1мм для МДФ фасадов
    mdf_facade_needs_edge: bool = True  # False если плёнка/краска (торец уже закрыт)

    # Фасады
    facades_area_m2: float = 0
    facade_type: Optional[str] = None  # "ПВХ IVEGO" / "EMDIWAY" / "Лакокраска"

    # Ручки накладные
    handles_count: int = 0

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

    # Столешница
    countertop_length_m: float = 0    # общая длина столешницы (м)
    countertop_depth_mm: int = 0      # глубина столешницы

    # Крепёж
    confirmat_count: int = 0          # конфирматы 7×50
    adjustable_feet: int = 0          # регулируемые опоры
    wall_mounts: int = 0              # настенные подвесы
    plinth_strips: int = 0            # планки цоколя ПВХ (4м)

    # Штанги (для гардеробных)
    rods_round_count: int = 0         # штанги круглые D25
    rods_rectangular_count: int = 0   # штанги прямоугольные

    # Комплектующие
    has_sink: bool = False
    bottle_holder_count: int = 0
    bottle_holder_type: str = ""       # "flora" / "kvadro" / "boyard"
    cutlery_tray_count: int = 0
    drying_rack_count: int = 0
    drying_rack_type: str = ""         # "alba" или "boyard"
    hygienic_mat_count: int = 0       # гигиенический коврик/поддон

    # Эргономика / рекомендации
    suggestions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def detect_material_properties(materials: List[str]) -> dict:
    """
    Единая точка определения свойств материала.

    Используется ВЕЗДЕ, где нужно понять: текстура или однотон,
    какой бренд, какой тип фасадов, есть ли стекло.

    Returns:
        {"surface": "plain"|"texture",
         "brand": "EGGER"|"EXTRAVERT"|"LAMARTY"|"ТОМЛЕСДРЕВ"|"unknown",
         "facade_type": "pvh"|"emdiway"|"emdiway_titan"|"paint_matte"|"paint_gloss"|"unknown",
         "has_glass": bool}
    """
    result = {
        "surface": "plain",
        "brand": "unknown",
        "facade_type": "unknown",
        "has_glass": False,
    }

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
    TEXTURE_KEYWORDS = [
        "ТЕКСТУР", "ДРЕВЕСН", "WOOD", "ДУБ", "ОРЕХ", "ЯСЕНЬ",
        "КАМЕНЬ", "STONE", "БЕТОН", "МРАМОР", "MARBLE", "ТКАНЬ",
        "МЕТАЛЛ", "METAL", "ПАТИН",
    ]
    if any(kw in materials_upper for kw in TEXTURE_KEYWORDS):
        result["surface"] = "texture"
    elif any(
        word.startswith(p)
        for m in materials
        for word in m.upper().replace("-", " ").replace("_", " ").split()
        for p in ["H1", "H3"]
    ):
        # H1xxx/H3xxx — древесные декоры EGGER (ищем как отдельные слова)
        result["surface"] = "texture"

    # ── Тип фасада ──
    if "EMDIWAY" in materials_upper:
        result["facade_type"] = "emdiway_titan" if "TITAN" in materials_upper else "emdiway"
    elif any(kw in materials_upper for kw in ["ЛАКОКРАСКА", "МАТОВЫЙ", "МАТОВ"]):
        result["facade_type"] = "paint_matte"
    elif "ГЛЯНЕЦ" in materials_upper or "ГЛЯНЦ" in materials_upper:
        result["facade_type"] = "paint_gloss"
    elif "ПВХ" in materials_upper:
        result["facade_type"] = "pvh"

    # ── Стекло ──
    result["has_glass"] = any(
        kw in materials_upper for kw in ["СТЕКЛО", "ЗЕРКАЛО", "GLASS", "MIRROR", "ВИТРИН"]
    )

    # ── Нужна ли кромка МДФ-фасадам ──
    # Плёнка ПВХ / крашеный МДФ → торец уже закрыт, кромка НЕ нужна
    # Плитный МДФ (EVOGLOSS, EMDIWAY, AGT, ETERNO, SM`ART, EGGER PERFECT SENSE) → торец открыт, кромка 1мм НУЖНА
    # ЛДСП-фасады → кромка 0.8мм (уже считается отдельно), 1мм МДФ-кромка НЕ нужна
    result["mdf_needs_edge"] = False  # по умолчанию НЕ нужна

    PLATED_MDF_BRANDS = ["EVOGLOSS", "EMDIWAY", "AGT", "ETERNO", "SM`ART", "EGGER PERFECT SENSE", "PERFECT SENSE"]
    for brand in PLATED_MDF_BRANDS:
        if brand in materials_upper:
            result["mdf_needs_edge"] = True
            break

    return result


def calculate_quantities(
    modules: List[RecognizedModule],
    room_name: str = "",
    materials: List[str] = None,
    *,
    zone_type: str = None,           # NEW: тип помещения из AI
    has_spec: bool = False,          # если есть spec.yaml — не автодобавлять
    auto_accessories: bool = True,   # авто-сушка, лоток, бутылочница
    auto_drawers: bool = True,       # авто-ящики если AI не нашёл
    auto_led: bool = True,           # авто-подсветка для кухни
    has_refrigerator: bool = False,  # встраиваемый холодильник
    has_oven: bool = False,          # духовка
    has_dishwasher: bool = False,    # посудомойка
    has_hood: bool = False,          # вытяжка
) -> MaterialQuantities:
    """
    Рассчитать количества материалов для списка модулей.

    Версия 3.0 — универсальный расчёт через FURNITURE_DEFAULTS:
    - Тип помещения (zone_type) управляет всеми эвристиками
    - Разные стандартные размеры для кухни/шкафа/гардероба/офиса
    - Раздвижные двери vs распашные (петли только для hinged)
    """
    q = MaterialQuantities()
    materials = materials or []

    # Правила для этого типа помещения
    rules = get_rules(zone_type)

    room_lower = room_name.lower()
    is_kitchen = rules.family == "kitchen"
    is_wardrobe = rules.family == "wardrobe"

    # Определяем свойства материала (единая функция)
    mat_props = detect_material_properties(materials)
    if mat_props["surface"] == "texture":
        q.ldsp_material = "текстура"

    # ── Размер листа по бренду ──
    brand = mat_props.get("brand", "unknown")
    sheet_ldsp = SHEET_LDSP_BY_BRAND.get(brand, SHEET_LDSP_BY_BRAND["default"])
    sheet_area_m2 = sheet_ldsp["area_m2"]
    q.ldsp_brand = brand if brand != "unknown" else ""

    # Коэффициент запаса
    is_texture = mat_props["surface"] == "texture"
    waste_ldsp = WASTE_FACTOR_TEXTURE if is_texture else WASTE_FACTOR_PLAIN

    total_hdf_area = 0.0
    lower_modules_width = 0.0
    lower_modules_count = 0
    visible_module_count = 0
    upper_modules_count = 0
    total_shelves_count = 0
    total_partitions_count = 0  # вертикальные перегородки

    for module in modules:
        # Размеры в мм (с припуском на пропил)
        w_mm = module.width + SAW_KERF_MM
        d_mm = module.depth + SAW_KERF_MM
        h_mm = module.height + SAW_KERF_MM
        w_m = w_mm / 1000
        d_m = d_mm / 1000
        h_m = h_mm / 1000
        qty = max(module.quantity, 1)

        # ── Детали корпуса (на 1 модуль) ──
        sides_area      = 2 * (d_m * h_m)        # 2 боковины
        bottom_top_area = 2 * (w_m * d_m)         # дно + крыша
        back_area       = (module.width / 1000) * (module.height / 1000)  # ХДФ без пропила
        shelf_count     = max(module.shelves, 1 if module.type in ("lower_base", "upper_base") else 0)
        shelf_area      = w_m * d_m * shelf_count

        ldsp_per_module = sides_area + bottom_top_area + shelf_area
        q.ldsp_area_m2 += ldsp_per_module * qty

        total_shelves_count += shelf_count * qty

        # ХДФ — суммируем площадь, пересчитаем в листы в конце
        total_hdf_area += back_area * qty

        # ── КРОМКА ЛДСП: ВСЕ торцы ВСЕХ деталей (кромим вкруг) ──
        # Периметры в метрах
        sides_perimeter      = 2 * (2 * (d_m + h_m))   # 2 боковины
        bottom_top_perimeter = 2 * (2 * (w_m + d_m))   # дно + крыша
        shelf_perimeter      = shelf_count * (2 * (w_m + d_m))
        back_perimeter       = 2 * ((module.width/1000) + (module.height/1000))  # задняя стенка

        total_perimeter = (sides_perimeter + bottom_top_perimeter + shelf_perimeter + back_perimeter) * qty

        # Видимые (передние) торцы корпуса
        visible_per_module = (2 * h_m + 2 * w_m)  # передние кромки боковин + дна/крыши
        visible_per_module += shelf_count * w_m   # передняя кромка каждой полки

        # Кромка фасадов (видимая со всех сторон)
        if module.facades and module.facades.get("count", 0) > 0:
            fc = module.facades.get("count", 1)
            fh = (module.height - 4) / 1000
            fw = (module.width / fc - 3) / 1000
            visible_per_module += (2 * fh + 2 * fw) * fc  # периметр всех фасадов

        visible_total = visible_per_module * qty
        premium_total = 0  # 2мм не используется по умолчанию

        q.edge_total_m    += total_perimeter * EDGE_OVERLAP
        q.edge_visible_m  += visible_total * EDGE_OVERLAP
        q.edge_premium_m  += premium_total * EDGE_OVERLAP

        # ── КРОМКА МДФ (1мм) — для плитного МДФ ──
        if mat_props.get("mdf_needs_edge", False) and module.facades and module.facades.get("count", 0) > 0:
            fc = module.facades.get("count", 1)
            fh = (module.height - 4) / 1000
            fw = (module.width / fc - 3) / 1000
            facade_perimeter = (2 * fh + 2 * fw) * fc * qty
            # Высокие фасады >2000мм → 2мм кромка
            if module.height > 2000:
                q.edge_2_m += facade_perimeter * EDGE_OVERLAP
            else:
                q.edge_mdf_1mm_m += facade_perimeter * EDGE_OVERLAP
        else:
            q.mdf_facade_needs_edge = False

        # ── Фасады ──
        if module.facades and module.facades.get("count", 0) > 0:
            facade_count = module.facades.get("count", 1)
            facade_h_m = (module.height - 4) / 1000   # зазор 4мм
            facade_w_m = (module.width / facade_count - 3) / 1000  # зазор 3мм
            q.facades_area_m2 += facade_w_m * facade_h_m * facade_count * qty

        # ── Плёнка ПВХ (если фасады IVEGO) ──
        if mat_props["facade_type"] == "pvh" and module.facades and module.facades.get("count", 0) > 0:
            facade_count = module.facades.get("count", 1)
            facade_h_m = (module.height - 4) / 1000
            facade_w_m = (module.width / facade_count - 3) / 1000
            q.pvc_film_m2 += facade_w_m * facade_h_m * facade_count * qty

        # ── Ручки (только для обычных фасадов на петлях) ──
        if module.type in ("lower_base", "upper_base", "penal", "column", "tumbler"):
            if module.facades and module.facades.get("count", 0) > 0:
                fc = module.facades.get("count", 1)
                # Gola и push-to-open — без ручек; обычные петли — с ручками
                # По умолчанию считаем что ручки нужны (Gola отсеивается отдельно — см. ниже)
                for _ in range(fc):
                    if module.height <= 1200:
                        q.handles_count += 1 * qty
                    else:
                        q.handles_count += 2 * qty

        # ── Петли: по высоте ОДНОЙ ДВЕРИ, а не модуля ──
        # Ящики = без петель (используют боксы)
        # Встраиваемая техника = петли в комплекте, НЕ считаем
        if module.type in ("lower_base", "upper_base", "penal", "column", "tumbler"):
            # Пропускаем модули-ящики (у них направляющие, не петли)
            has_drawers_only = (
                module.drawers
                and module.drawers.get("count", 0) > 0
                and not (module.facades and module.facades.get("count", 0) > 0)
            )
            # Пропускаем пеналы под встраиваемую технику
            is_appliance_penal = (
                module.type == "penal"
                and module.width >= 600
                # Если пенал без фасадов — под технику, петли в комплекте
                and not (module.facades and module.facades.get("count", 0) > 0)
            )

            if not has_drawers_only and not is_appliance_penal and module.facades:
                facade_count = module.facades.get("count", 1)
                # Высота ОДНОЙ двери = высота модуля / количество дверей
                single_door_h = module.height / facade_count

                # Правила заказчика (не Blum, не по весу):
                if single_door_h >= 2000:
                    hinges_per_door = 4     # пеналы: 4 петли
                elif single_door_h > 900:
                    hinges_per_door = 3     # высокие фасады: 3 петли
                elif single_door_h > 600:
                    hinges_per_door = 2     # стандартные: 2 петли
                else:
                    hinges_per_door = 2

                q.hinges_count += hinges_per_door * facade_count * qty

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

        # Счётчики для Gola, столешницы, крепежа
        if module.type == "lower_base":
            lower_modules_width += (module.width / 1000) * qty
            lower_modules_count += qty
            # Вертикальные перегородки в нижних базах
            if module.facades and module.facades.get("count", 1) > 1:
                total_partitions_count += (module.facades.get("count", 1) - 1) * qty
        if module.type == "upper_base":
            upper_modules_count += qty
            if module.facades and module.facades.get("count", 1) > 1:
                total_partitions_count += (module.facades.get("count", 1) - 1) * qty
        if module.type in ("lower_base", "upper_base", "penal"):
            visible_module_count += qty

    # ── УЧЁТ СМЕЖНЫХ СТЕНОК ──
    # Соседние модули одного типа и глубины делят боковину
    adjacency_groups: Dict[str, List[RecognizedModule]] = {}
    for m in modules:
        key = f"{m.type}_{m.depth}"
        adjacency_groups.setdefault(key, []).append(m)

    shared_sides_saved = 0
    shared_area_saved_m2 = 0.0
    for key, mods in adjacency_groups.items():
        n = sum(max(m.quantity, 1) for m in mods)
        if n >= 2:
            avg_h = sum(m.height * max(m.quantity, 1) for m in mods) / max(n, 1)
            avg_d = (mods[0].depth + SAW_KERF_MM) / 1000  # глубина в метрах с пропилом
            potential_joints = n - 1
            actual_joints = int(potential_joints * 0.7)  # 70% потенциальных стыков
            area_per_joint = avg_d * ((avg_h + SAW_KERF_MM) / 1000)  # м²
            shared_sides_saved += actual_joints
            shared_area_saved_m2 += actual_joints * area_per_joint

    if shared_area_saved_m2 > 0:
        q.ldsp_area_m2 -= shared_area_saved_m2
        logger.info(
            f"Учтено смежных стенок: {shared_sides_saved} стыков, "
            f"экономия {shared_area_saved_m2:.2f} м² ЛДСП"
        )

    # ── ИТОГОВЫЕ РАСЧЁТЫ ──

    # Фасады из ЛДСП: если не МДФ/ПВХ/лакокраска — добавляем в ЛДСП
    if mat_props["facade_type"] == "unknown" and q.facades_area_m2 > 0:
        q.ldsp_area_m2 += q.facades_area_m2 * 1.15  # +15% на облицовку кромок фасадов
        logger.info(f"Фасады ЛДСП: +{q.facades_area_m2:.1f} м² → ЛДСП")

    # ЛДСП: листы с запасом на раскрой
    q.ldsp_sheets = max(1, math.ceil(q.ldsp_area_m2 * waste_ldsp / sheet_area_m2)) if q.ldsp_area_m2 > 0 else 0

    # МДФ: листы для фасадов
    if q.facades_area_m2 > 0 and mat_props["facade_type"] not in ("unknown", "pvh"):
        # Для больших фасадов используем большой формат МДФ
        mdf_sheet = SHEET_MDF_LARGE if q.facades_area_m2 > 3 else SHEET_MDF_STANDARD
        q.mdf_sheets = math.ceil(q.facades_area_m2 * WASTE_FACTOR_MDF / mdf_sheet["area_m2"])

    # Плёнка ПВХ: +10% запас
    if q.pvc_film_m2 > 0:
        q.pvc_film_m2 = round(q.pvc_film_m2 * WASTE_FACTOR_PVC, 1)

    # ХДФ: для кухонь — минус площадь под технику/мойку
    if total_hdf_area > 0:
        if is_kitchen or rules.family == "bathroom":
            # Вычитаем площадь задней стенки за техникой и мойкой (~40%)
            excluded = 0.0
            if has_refrigerator:
                excluded += 0.6 * 1.8   # ~холодильник 600×1800
            if has_oven:
                excluded += 0.6 * 0.6    # ~духовка 600×600
            if has_dishwasher:
                excluded += 0.45 * 0.82  # ~посудомойка 450×820
            if has_hood:
                excluded += 0.6 * 0.3    # ~вытяжка 600×300
            if q.has_sink:
                excluded += 0.6 * 0.82   # ~мойка 600×820
            total_hdf_area = max(0, total_hdf_area - excluded)
        q.hdf_sheets = max(1, math.ceil(total_hdf_area * WASTE_FACTOR_HDF / SHEET_MDF_STANDARD["area_m2"]))

    # Кромка ЛДСП: распределяем
    q.edge_2_m  = math.ceil(q.edge_premium_m)
    q.edge_08_m = math.ceil(q.edge_visible_m)
    q.edge_04_m = math.ceil(q.edge_total_m - q.edge_visible_m - q.edge_premium_m)
    if q.edge_04_m < 0:
        q.edge_04_m = 0

    # Кромка МДФ: округляем
    q.edge_mdf_1mm_m = math.ceil(q.edge_mdf_1mm_m)

    # ── СТОЛЕШНИЦА (только для кухонь/ванных) ──
    if rules.auto_countertop and lower_modules_width > 0:
        # Длина = сумма ширин нижних баз + 50мм на стык + 10% на подрезку
        num_joints = max(0, lower_modules_count - 1)
        q.countertop_length_m = (lower_modules_width + num_joints * 0.05) * 1.10
        # Глубина = стандартная глубина нижних баз + 40мм свес
        q.countertop_depth_mm = 600 + 40  # стандарт 600 + свес 40

    # ── КРЕПЁЖ ──
    # Конфирматы 7×50: полки×4 + крышки×6 + днища×6 + перегородки×4 + 10%
    lids_count = sum(max(m.quantity, 1) for m in modules)  # ≈ количество модулей = количество крышек
    bottoms_count = lids_count  # столько же днищ
    confirmat_base = (total_shelves_count * 4) + (lids_count * 6) + (bottoms_count * 6) + (total_partitions_count * 4)
    q.confirmat_count = math.ceil(confirmat_base * 1.10)

    # Регулируемые опоры: 4 на нижнюю базу, 6 на широкую (>900мм)
    for m in modules:
        if m.type == "lower_base":
            qty = max(m.quantity, 1)
            feet_per = 6 if m.width > 900 else 4
            q.adjustable_feet += feet_per * qty

    # Настенные подвесы: 2 на каждую верхнюю базу
    q.wall_mounts = upper_modules_count * 2

    # Цоколь ПВХ: суммарная ширина нижних баз / 4м + 10%
    if lower_modules_width > 0:
        q.plinth_strips = math.ceil(lower_modules_width * 1.10 / 4)

    # ── Gola: вертикальные + горизонтальные (только для kitchen_family) ──
    if rules.auto_gola and not has_spec:
        # Горизонтальный Gola: сумма ширин нижних баз
        q.gola_horizontal_m = lower_modules_width

        # Вертикальный Gola: открытые торцы
        if visible_module_count > 0 and modules:
            avg_height_m = sum(
                (m.height / 1000) * max(m.quantity, 1)
                for m in modules
                if m.type in ("lower_base", "upper_base", "penal")
            ) / max(visible_module_count, 1)

            vertical_count = 2  # базовые левый + правый край
            has_corner = any(m.is_corner or m.type == "corner" for m in modules)
            if has_corner:
                vertical_count += 1  # угол создаёт дополнительный открытый торец
            has_island = "остров" in room_lower
            if has_island:
                vertical_count += 2

            q.gola_vertical_m = avg_height_m * vertical_count
            q.gola_vertical_pcs = vertical_count

        # Штуки по 3 метра
        q.gola_horizontal_pcs = math.ceil(q.gola_horizontal_m / 3) if q.gola_horizontal_m > 0 else 0

        # LED: если spec уже добавил LED — не дублируем блоки питания/датчики
        if auto_led and not has_spec and q.led_strip_m == 0:
            q.led_strip_m      = q.gola_horizontal_m * 0.7
            if q.led_strip_m > 0:
                led_watt = q.led_strip_m * 9.6 * 1.2  # мощность с запасом 20%
                if q.led_power_supply == 0:
                    q.led_power_supply = max(1, math.ceil(led_watt / 100))
                if q.led_sensor == 0:
                    q.led_sensor = 1

    # ── Отключаем ручки для Gola и push-to-open ──
    # Если есть Gola — ручки не нужны
    if q.gola_horizontal_pcs > 0 or q.gola_vertical_pcs > 0:
        q.handles_count = 0

    # ── Эргономика / рекомендации (на основе FURNITURE_DEFAULTS) ──

    # Кухня: НЕ дублируем если spec уже добавил
    if is_kitchen:
        if auto_drawers and not has_spec and q.drawers_count == 0 and q.drawers_internal_count == 0:
            q.drawers_count = 2
            q.drawers_internal_count = 1
            q.drawer_system = "Tandembox"
            q.suggestions.append(
                "🍴 Рекомендация: добавить 2 ящика Tandembox — "
                "один стандартный, один с внутренним для столовых приборов"
            )

        if auto_accessories and not has_spec:
            if q.cutlery_tray_count == 0:
                q.cutlery_tray_count = 1
            if q.bottle_holder_count == 0:
                q.bottle_holder_count = 1 if any(m.width <= 200 for m in modules) else 0
                q.bottle_holder_type = "flora"
            q.has_sink = True
            if q.has_sink and q.drying_rack_count == 0:
                q.drying_rack_count = 1
                q.drying_rack_type = "alba"
                q.suggestions.append("💧 Рекомендация: сушка для посуды в верхнюю базу")

        has_upper = any(m.type == "upper_base" for m in modules)
        has_lower = any(m.type == "lower_base" for m in modules)
        if has_upper:
            q.suggestions.append("📦 Верхние базы: посуда, чашки, специи, лёгкие продукты")
        if has_lower:
            q.suggestions.append("📦 Нижние базы: кастрюли, сковородки, бытовая химия, мойка")

        if q.countertop_length_m > 0:
            q.suggestions.append(
                f"🪚 Столешница: ~{q.countertop_length_m:.1f}м × {q.countertop_depth_mm}мм "
                f"(постформинг / искусственный камень — уточнить)"
            )

    # Шкафы / гардеробные: штанги
    if rules.auto_rods:
        modules_for_rods = [m for m in modules if m.width >= 450]
        if modules_for_rods:
            q.rods_round_count = len(modules_for_rods)
            q.suggestions.append(f"👔 Рекомендация: {len(modules_for_rods)} штанг D25 для одежды")

    # Открытые модули: доп. полки
    if rules.auto_shelves:
        open_mods = [m for m in modules if m.type in ("shelf_unit", "open_unit")]
        if open_mods:
            q.suggestions.append(f"📚 Рекомендация: проверить количество полок в открытых модулях")

    if is_wardrobe:
        q.suggestions.append(
            "👔 Рекомендация: штанга для одежды + полки для обуви в нижней зоне"
        )
        # Штанги
        for m in modules:
            if m.type == "penal" and m.width >= 600:
                q.rods_rectangular_count += 1 * max(m.quantity, 1)
                q.suggestions.append(
                    f"👕 Штанга прямоугольная в пенал {m.width}мм — {m.quantity} шт."
                )
            elif m.type in ("lower_base", "upper_base") and m.width >= 600:
                q.rods_round_count += 1 * max(m.quantity, 1)

    return q


def fill_template_for_room(
    modules: List[RecognizedModule],
    room_name: str,
    materials: List[str],
    *,
    zone_type: str = None,
) -> List[Tuple[str, str, str, float, float]]:
    """
    Заполнить позиции шаблона для одного помещения.

    Returns:
        Список кортежей: (категория, наименование, цвет/поставщик, цена, количество)
    """
    q = calculate_quantities(modules, room_name, materials, zone_type=zone_type)
    items = []

    rules = get_rules(zone_type)
    is_kitchen = rules.family == "kitchen"

    # Определяем материал через единую функцию
    mat_props = detect_material_properties(materials)
    is_texture = mat_props["surface"] == "texture"
    ldsp_material = "EGGER текстура" if is_texture else "EGGER однотон"
    ldsp_price = 7200 if is_texture else 6000

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
        # Определяем тип фасада через единую функцию
        FACADE_PRICES = {
            "emdiway": ("EMDIWAY однотонный", 9800),
            "emdiway_titan": ("EMDIWAY Titan", 11200),
            "paint_matte": ("Лакокраска матовая", 10450),
            "paint_gloss": ("Лакокраска глянец", 11500),
            "pvh": ("ФАСАДЫ ПВХ 16мм", 5729),
        }
        facade_name, facade_price = FACADE_PRICES.get(
            mat_props["facade_type"],
            ("ФАСАДЫ ПВХ 16мм", 5729)
        )

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

    # ── 10. Столешница ──
    if q.countertop_length_m > 0:
        items.append(("СТОЛЕШНИЦА", f"Столешница постформинг 38мм, ~{q.countertop_length_m:.1f}м × {q.countertop_depth_mm}мм",
                      "требуется уточнение цены", 0, round(q.countertop_length_m, 1)))

    # ── 11. Крепёж ──
    if q.confirmat_count > 0:
        items.append(("КРЕПЁЖ", "Конфирмат 7×50",
                      f"{q.confirmat_count} шт × 2₽", 2, q.confirmat_count))
    if q.adjustable_feet > 0:
        items.append(("КРЕПЁЖ", "Регулируемая опора",
                      f"{q.adjustable_feet} шт × 50₽", 50, q.adjustable_feet))
    if q.wall_mounts > 0:
        items.append(("КРЕПЁЖ", "Настенный подвес (регулируемый)",
                      f"{q.wall_mounts} шт × 120₽", 120, q.wall_mounts))
    if q.plinth_strips > 0:
        items.append(("КРЕПЁЖ", "Цоколь ПВХ Rehau 4м",
                      f"{q.plinth_strips} шт × 1500₽", 1500, q.plinth_strips))

    # ── 12. Штанги ──
    if q.rods_round_count > 0:
        items.append(("ШТАНГИ", "Штанга круглая D25",
                      f"{q.rods_round_count} шт × 407₽", 407, q.rods_round_count))
    if q.rods_rectangular_count > 0:
        items.append(("ШТАНГИ", "Штанга прямоугольная антискользящая 3000мм",
                      f"{q.rods_rectangular_count} шт × 1700₽", 1700, q.rods_rectangular_count))

    # ── 13. Ручки ──
    if q.handles_count > 0:
        items.append(("РУЧКИ", "Ручка накладная",
                      f"{q.handles_count} шт × 500₽", 500, q.handles_count))

    # ── 14. Плёнка ПВХ ──
    if q.pvc_film_m2 > 0:
        items.append(("ПЛЁНКА ПВХ", "Плёнка ПВХ IVEGO I категория",
                      f"{q.pvc_film_m2:.1f} м² × 3990₽", 3990, round(q.pvc_film_m2, 1)))

    # ── 15. Кромка МДФ ──
    if q.edge_mdf_1mm_m > 0:
        items.append(("КРОМКА МДФ", "Кромка МДФ 1*22",
                      f"{q.edge_mdf_1mm_m:.0f} м.п. × 45₽", 45, q.edge_mdf_1mm_m))

    return items
