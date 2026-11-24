"""
Калькулятор количества материалов на основе AI-модулей.

Переводит модули (600×560×820) в реальные позиции прайса:
- Листы ЛДСП (площадь деталей / площадь листа × k запаса на раскрой)
- Кромка (ВСЕ торцы всех деталей — кромим вкруг; 0 для МДФ в плёнке/краске)
- Фасады (площадь фасадов × цена за м²; плёнка ПВХ IVEGO)
- Петли (таблица: высота + вес фасада → N петель)
- Ручки (Gola → 0, push-to-open → 0, обычные ≤1200→1, >1200→2)
- Раздвижные двери (door_system="sliding": шкаф-купе, гардеробные) — БЕЗ
  петель и накладных ручек: створки ездят на системе направляющих/роликов
  (позиции раздвижной системы в прайсе нет — не выдумываем цену)
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

import logging
import math
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

from app.services.furniture_defaults import get_rules
from app.services.hardware_calc import (
    DEFAULT_HINGE_BRAND,
    estimate_facade_weight_kg,  # оценка веса фасада (BLUM/HETTICH + вес)
    hinges_per_door,  # единое правило петель (parity с calc_engine)
)
from app.services.image_analyzer import RecognizedModule

# ═══════════════════════════════════════════════════════════════════
# РАЗМЕРЫ ЛИСТОВ ПО ПРОИЗВОДИТЕЛЮ
# ═══════════════════════════════════════════════════════════════════

# ЛДСП: разные производители — разные размеры листов
SHEET_LDSP_BY_BRAND = {
    "EGGER":     {"width_mm": 2750, "height_mm": 1830, "area_m2": 5.0325},
    "EXTRAVERT": {"width_mm": 2750, "height_mm": 1830, "area_m2": 5.0325},
    "LAMARTY":   {"width_mm": 2800, "height_mm": 2070, "area_m2": 5.796},
    "EMDIWAY":   {"width_mm": 2800, "height_mm": 2070, "area_m2": 5.796},
    # P1-08: SLOTEX — свой формат (раньше падал в default 5,796 м² и завышал
    # число листов). 3000×1200 = 3,6 м²; длинный 4200×600 = 2,52 м².
    "SLOTEX":    {"width_mm": 3000, "height_mm": 1200, "area_m2": 3.6},
    "default":   {"width_mm": 2800, "height_mm": 2070, "area_m2": 5.796},
}

# Длинный формат SLOTEX (4200×600 = 2,52 м²) — для узких длинных деталей.
SHEET_LDSP_SLOTEX_LONG = {"width_mm": 4200, "height_mm": 600, "area_m2": 2.52}

# МДФ: стандартный лист + большой формат
SHEET_MDF_STANDARD = {"width_mm": 2800, "height_mm": 1220, "area_m2": 3.416}
SHEET_MDF_LARGE    = {"width_mm": 3050, "height_mm": 2070, "area_mm2": 3050*2070, "area_m2": 6.3135}

# P1-04: все типы модулей, у которых бывают распашные двери. Раньше список
# ограничивался lower_base/upper_base/penal/column/tumbler, поэтому «Тумба»
# (cabinet), шкаф, пенал-колонна и тумба под раковину оставались без петель
# и ручек даже при дверях.
HINGED_MODULE_TYPES = {
    "lower_base", "upper_base", "penal", "column", "tumbler",
    "cabinet", "wardrobe", "tall_cabinet", "vanity", "drawer_unit", "corner",
}

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

# Нормы кромки на ОДИН корпус, метры: (0.8 видимая, 0.4 скрытая).
# Выведены из расчётов Olga_project/Волонтерский центр, где известно
# количество штук в позиции: шкаф для бумаг 350/200 на 5 корпусов,
# столы 350/250 на 8, трибуна 50/20, ресепшн 150/80, шкаф под одежду 80/80.
# Гибрид: считаем геометрию, но не ниже нормы на тип.
# Включается переменной окружения EDGE_NORMS=1 (гибрид кромки).
EDGE_NORMS_ENABLED = os.getenv("EDGE_NORMS", "0") == "1"

EDGE_NORMS_M: dict[str, tuple[float, float]] = {
    "penal": (70.0, 40.0),
    "wardrobe": (80.0, 80.0),
    "tall_cabinet": (60.0, 40.0),
    "table": (44.0, 31.0),
    "desk": (44.0, 31.0),
    "open_unit": (50.0, 20.0),
    "shelf_unit": (60.0, 50.0),
    "reception": (150.0, 80.0),
    "counter": (150.0, 80.0),
    "lower_base": (50.0, 35.0),
    "upper_base": (40.0, 28.0),
    "vanity": (45.0, 30.0),
}

# Запас кромки на обработку (30% по промпту v4.0)
EDGE_OVERLAP = 1.30


@dataclass
class MaterialQuantities:
    """Рассчитанные количества материалов."""
    # ЛДСП
    ldsp_area_m2: float = 0
    ldsp_sheets: int = 0
    ldsp_material: str | None = None  # "EGGER однотон" / "EGGER текстура"
    ldsp_brand: str = ""                 # "EGGER" / "EXTRAVERT" / "LAMARTY"

    # МДФ (фасады)
    mdf_area_m2: float = 0
    mdf_sheets: int = 0
    mdf_material: str | None = None   # "EMDIWAY" / "EVOGLOSS" / "AGT" / etc.

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
    facade_type: str | None = None  # "ПВХ IVEGO" / "EMDIWAY" / "Лакокраска"

    # Ручки накладные
    handles_count: int = 0
    # P1-07: способ открывания и механизмы вместо ручек.
    handle_mode: str = "handles"     # handles | gola | push | lift
    push_open_count: int = 0          # push-to-open механизмы (по петлям)
    lift_mechanism_count: int = 0     # подъёмники (lift)

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
    suggestions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def detect_material_properties(materials: list[str]) -> dict:
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
        # Пустой список материалов — неизвестность; консервативно считаем,
        # что торец МДФ-фасада нуждается в кромке (ключ всегда присутствует).
        "mdf_needs_edge": True,
    }

    if not materials:
        return result

    materials_upper = " ".join(m.upper() for m in materials)

    # ── Бренд ЛДСП ──
    for brand in ["EGGER", "EXTRAVERT", "LAMARTY", "SLOTEX"]:
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
    # Плитные МДФ-бренды различаются для выбора ОДНОЙ строки кромки в Excel
    # (P0-06): EVO/AGT/ETERNO/SM`ART — разные поставщики.
    # EMDIWAY в прайсе поставщика пишется и как EMDIWEY/EMDIVEY — это тот же
    # плитный МДФ, иначе фасады получают facade_type=unknown.
    if any(alias in materials_upper for alias in ("EMDIWAY", "EMDIWEY", "EMDIVEY")):
        result["facade_type"] = "emdiway_titan" if "TITAN" in materials_upper else "emdiway"
    elif "EVOGLOSS" in materials_upper or "EVO " in materials_upper:
        result["facade_type"] = "evo"
    elif "EVOSOFT" in materials_upper or "EVO SOFT" in materials_upper:
        # EVOSOFT — МДФ в ПВХ-плёнке: торец закрыт, кромка 1 мм не нужна.
        result["facade_type"] = "pvh"
    elif "AGT" in materials_upper:
        result["facade_type"] = "agt"
    elif "ETERNO" in materials_upper:
        result["facade_type"] = "eterno"
    elif "SM`ART" in materials_upper or "SMART" in materials_upper:
        result["facade_type"] = "smart"
    elif any(kw in materials_upper for kw in ["ЛАКОКРАСКА", "МАТОВЫЙ", "МАТОВ"]):
        result["facade_type"] = "paint_matte"
    elif "ГЛЯНЕЦ" in materials_upper or "ГЛЯНЦ" in materials_upper:
        result["facade_type"] = "paint_gloss"
    elif any(kw in materials_upper for kw in ["ПВХ", "ПЛЁНКА", "ПЛЕНКА"]):
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

    PLATED_MDF_BRANDS = ["EVOGLOSS", "EMDIWAY", "EMDIWEY", "EMDIVEY", "AGT", "ETERNO", "SM`ART", "EGGER PERFECT SENSE", "PERFECT SENSE"]
    for brand in PLATED_MDF_BRANDS:
        if brand in materials_upper:
            result["mdf_needs_edge"] = True
            break

    return result


def _estimate_door_weight_kg(module, facade_count: int, mat_props: dict) -> float:
    """Оценка веса ОДНОЙ двери (кг) для высотно-весовой логики BLUM/HETTICH.

    Дверь считается по габаритам модуля (facades.count описывает двери РЯДОМ,
    каждая — полная высота модуля): ширина = module.width / facade_count,
    высота = module.height. Материал фасада определяется из mat_props:
      - ПВХ-плёнка (pvh) → ЛДСП (плотность ldsp);
      - EMDIWAY / крашеный МДФ (emdiway/paint_*) → МДФ (mdf);
      - стекло/зеркало → стекло (glass).
    Если материал фасада не определён → возвращаем None: вес «неизвестен»,
    и петли считаются по чисто высотной таблице (ровно как раньше).
    Толщина фасада берётся типовая по материалу (estimate_facade_weight_kg).
    """
    door_width_mm = module.width / max(facade_count, 1)
    door_height_mm = module.height

    facade_type = (mat_props or {}).get("facade_type", "unknown")
    has_glass = bool((mat_props or {}).get("has_glass", False))
    has_glass = has_glass or bool(getattr(module, "has_glass", False))

    if facade_type == "pvh":
        material = "ldsp"      # ПВХ-фасад на ЛДСП
    elif facade_type in ("emdiway", "emdiway_titan", "paint_matte", "paint_gloss"):
        material = "mdf"       # плитный/крашеный МДФ (EMDIWAY, EVOGLOSS, AGT…)
    elif has_glass:
        material = "glass"     # стеклянная дверь/витрина (оценка по стеклу)
    else:
        return None            # материал не определён → вес неизвестен

    return estimate_facade_weight_kg(
        width_mm=door_width_mm,
        height_mm=door_height_mm,
        material=material,
    )


def module_has_drawers(module: dict) -> bool:
    """Есть ли в модуле ящики — по любой из трёх схем (P1-06).

    Распознавание (image.py) хранит `drawers: {"count": N}` (или список),
    встречается `drawer_counts`, ручной ввод — `filling="drawers"`.
    Раньше проверялся только `filling`, поэтому AI-модули с ящиками не
    доходили до выбора системы и КП блокировался на «Ящики без цены».
    """
    drawers = module.get("drawers")
    if isinstance(drawers, dict) and int(drawers.get("count") or 0) > 0:
        return True
    if isinstance(drawers, (list, tuple)) and len(drawers) > 0:
        return True
    if (
        isinstance(drawers, (int, float))
        and not isinstance(drawers, bool)
        and drawers > 0
    ):
        return True
    counts = module.get("drawer_counts")
    if isinstance(counts, dict) and any(int(v or 0) > 0 for v in counts.values()):
        return True
    if isinstance(counts, (list, tuple)) and any(int(v or 0) > 0 for v in counts):
        return True
    return str(module.get("filling") or "").strip().lower() == "drawers"


def calculate_quantities(
    modules: list[RecognizedModule],
    room_name: str = "",
    materials: list[str] | None = None,
    *,
    # P1-08: бренд/формат ЛДСП определяются ТОЛЬКО по выбранной позиции
    # «ЛДСП», а не по объединённому списку (кромка EGGER больше не делает
    # ЛДСП SLOTEX «EGGER» и не меняет число листов).
    ldsp_name: str | None = None,
    drawer_system: str | None = None,        # P1-06: выбранная система ящиков
    handle_mode: str | None = None,          # P1-07: handles | gola | push | lift
    zone_type: str | None = None,           # NEW: тип помещения из AI
    has_spec: bool = False,          # если есть spec.yaml — не автодобавлять
    # P1-02: платные автопозиции — opt-in. По умолчанию False: раньше кухня
    # молча получала 2+1 ящика, сушку, лоток, бутылочницу, GOLA и LED
    # (до +36 366 ₽ позиций, которых в проекте нет).
    auto_accessories: bool = False,  # авто-сушка, лоток, бутылочница
    auto_drawers: bool = False,      # авто-ящики если AI не нашёл
    auto_led: bool = False,          # авто-подсветка для кухни
    auto_gola: bool = False,         # авто-GOLA (профиль-ручка) для кухни
    has_sink: bool = False,          # мойка (для вычета ХДФ, P2-01)
    has_refrigerator: bool = False,  # встраиваемый холодильник
    has_oven: bool = False,          # духовка
    has_dishwasher: bool = False,    # посудомойка
    has_hood: bool = False,          # вытяжка
    hinge_brand: str = DEFAULT_HINGE_BRAND,  # бренд петель (parity с calc_engine):
                                            # "FIRMAX" — правила заказчика (по высоте),
                                            # "BLUM"/"HETTICH" — высота + вес фасада
) -> MaterialQuantities:
    """
    Рассчитать количества материалов для списка модулей.

    Версия 3.0 — универсальный расчёт через FURNITURE_DEFAULTS:
    - Тип помещения (zone_type) управляет всеми эвристиками
    - Разные стандартные размеры для кухни/шкафа/гардероба/офиса
    - Раздвижные двери vs распашные (петли только для hinged)
    """
    q = MaterialQuantities()
    mode = str(handle_mode or "handles").strip().lower()
    if mode not in {"handles", "gola", "push", "lift"}:
        mode = "handles"
    q.handle_mode = mode
    if drawer_system:
        q.drawer_system = drawer_system
    q.has_sink = bool(has_sink)  # до расчёта ХДФ, а не после (P2-01)
    materials = materials or []

    # Правила для этого типа помещения
    rules = get_rules(zone_type)

    room_lower = room_name.lower()
    is_kitchen = rules.family == "kitchen"
    is_wardrobe = rules.family == "wardrobe"
    # P1-04: раздвижная система — значение по умолчанию зоны, которое можно
    # переопределить на уровне модуля (`door_system="hinged"`), см.
    # is_sliding_module в цикле.

    # Определяем свойства материала (единая функция)
    mat_props = detect_material_properties(materials)
    # P1-08: свойства ЛДСП (бренд, формат, текстура) — только из позиции ЛДСП.
    brand_props = (
        detect_material_properties([ldsp_name]) if ldsp_name else mat_props
    )
    if brand_props["surface"] == "texture":
        q.ldsp_material = "текстура"

    # ── Размер листа по бренду ──
    brand = brand_props.get("brand", "unknown")
    sheet_ldsp = SHEET_LDSP_BY_BRAND.get(brand, SHEET_LDSP_BY_BRAND["default"])
    if brand == "unknown":
        q.warnings.append(
            "Бренд ЛДСП не определён: использован формат листа по умолчанию "
            f"{sheet_ldsp['width_mm']}×{sheet_ldsp['height_mm']} мм — проверьте"
        )
    elif brand not in SHEET_LDSP_BY_BRAND:
        q.warnings.append(
            f"Формат листа для бренда {brand} не задан: использован формат "
            f"по умолчанию {sheet_ldsp['width_mm']}×{sheet_ldsp['height_mm']} мм"
        )
    sheet_area_m2 = sheet_ldsp["area_m2"]
    q.ldsp_brand = brand if brand != "unknown" else ""

    # Коэффициент запаса
    is_texture = brand_props["surface"] == "texture"
    waste_ldsp = WASTE_FACTOR_TEXTURE if is_texture else WASTE_FACTOR_PLAIN

    total_hdf_area = 0.0
    lower_modules_width = 0.0
    lower_modules_count = 0
    visible_module_count = 0
    upper_modules_count = 0
    total_shelves_count = 0
    total_partitions_count = 0  # вертикальные перегородки

    for module in modules:
        # P2-14: модуль 0×0×0 раньше давал 1 лист и смету ~36 000 ₽.
        # Ширина/высота — обязательные габариты: без них нельзя построить
        # ни одной детали, поэтому это ошибка, а не «примерно 0».
        if module.width <= 0 or module.height <= 0:
            raise ValueError(
                "Размеры модуля должны быть больше нуля: "
                f"тип {module.type!r}, Ш×В = {module.width}×{module.height} мм"
            )
        if module.depth < 0:
            raise ValueError(
                "Глубина модуля не может быть отрицательной: "
                f"тип {module.type!r}, Г = {module.depth} мм"
            )
        # Размеры в мм (с припуском на пропил)
        w_mm = module.width + SAW_KERF_MM
        d_mm = module.depth + SAW_KERF_MM
        h_mm = module.height + SAW_KERF_MM
        w_m = w_mm / 1000
        d_m = d_mm / 1000
        h_m = h_mm / 1000
        qty = max(module.quantity, 1)

        # Стол — не корпус: столешница + боковины, без дна/крыши, полок и
        # задней стенки ХДФ. Иначе стол считался как шкаф (+33 % к себестоимости).
        is_table = module.type in ("table", "desk")
        # Стойка/ресепшн — двусторонняя: передняя и задняя панели.
        is_counter = module.type in ("reception", "counter")

        # ── Детали корпуса (на 1 модуль) ──
        sides_area      = 2 * (d_m * h_m)        # 2 боковины
        bottom_top_area = w_m * d_m if is_table else 2 * (w_m * d_m)
        back_area       = (
            0.0 if (is_table or is_counter)
            else (module.width / 1000) * (module.height / 1000)  # ХДФ без пропила
        )
        # P2-10: модуль «только ящики» (без дверей) не получает полку по
        # умолчанию — иначе ящичный корпус оплачивал лишнюю полку ЛДСП.
        facades_n = int(module.facades.get("count") or 0) if module.facades else 0
        drawers_n = int(module.drawers.get("count") or 0) if module.drawers else 0
        drawers_only = drawers_n > 0 and facades_n == 0
        shelf_count     = 1 if is_counter else 0 if is_table else max(
            module.shelves,
            rules.shelf_default
            if module.type in ("lower_base", "upper_base") and not drawers_only
            else 0,
        )
        shelf_area      = w_m * d_m * shelf_count

        front_back_area = 2 * (w_m * h_m) if is_counter else 0.0
        ldsp_per_module = sides_area + bottom_top_area + shelf_area + front_back_area
        q.ldsp_area_m2 += ldsp_per_module * qty

        total_shelves_count += shelf_count * qty

        # ХДФ — суммируем площадь, пересчитаем в листы в конце
        total_hdf_area += back_area * qty

        # ── КРОМКА ЛДСП: ВСЕ торцы ВСЕХ деталей (кромим вкруг) ──
        # Периметры в метрах
        sides_perimeter      = 2 * (2 * (d_m + h_m))   # 2 боковины
        bottom_top_perimeter = (1 if is_table else 2) * (2 * (w_m + d_m))
        shelf_perimeter      = shelf_count * (2 * (w_m + d_m))
        back_perimeter = (
            2 * ((module.width / 1000) + (module.height / 1000))
            if rules.back_wall_in_edge else 0.0
        )

        total_perimeter = (sides_perimeter + bottom_top_perimeter + shelf_perimeter + back_perimeter) * qty
        if is_counter:
            # передняя и задняя панели стойки — тоже кромятся вкруг
            total_perimeter += 2 * (2 * (w_m + h_m)) * qty

        # Видимые (передние) торцы корпуса
        visible_per_module = (
            (w_m + 2 * h_m) if is_table
            else (2 * h_m + 2 * w_m)  # передние кромки боковин + дна/крыши
        )
        if is_counter:
            visible_per_module += 2 * (w_m + h_m)  # передняя панель стойки
        visible_per_module += shelf_count * w_m   # передняя кромка каждой полки

        # P0-03: стеклянная дверь оплачивается как стекло, а не как фасад
        # из ЛДСП/МДФ. Иначе витрина платила дважды (фасад + стекло).
        facade_is_glass = bool(
            getattr(module, "has_glass", False)
            or (
                module.facades
                and str(module.facades.get("type") or "").lower() == "glass"
            )
        )

        # Кромка фасадов (видимая со всех сторон).
        # P2-08: если фасад плитный МДФ (EMDIWAY/EVOGLOSS/AGT…), его периметр
        # уходит в кромку МДФ 1 мм ниже. Раньше он одновременно попадал
        # и в edge_08, и в edge_mdf_1mm — двойной метраж.
        facade_uses_mdf_edge = bool(
            mat_props.get("mdf_needs_edge")
            and module.facades
            and module.facades.get("count", 0) > 0
            and not facade_is_glass
        )
        if (
            module.facades
            and module.facades.get("count", 0) > 0
            and not facade_is_glass
        ):
            fc = module.facades.get("count", 1)
            fh = (module.height - 4) / 1000
            fw = (module.width / fc - 3) / 1000
            if not facade_uses_mdf_edge:
                facade_perimeter = (2 * fh + 2 * fw) * fc
                visible_per_module += facade_perimeter  # периметр всех фасадов
                # P2-12: фасадные торцы — тоже часть «вкруг». Без этого
                # edge_04 = total − visible уменьшался при добавлении дверей
                # (16 → 12 → 10 м на одном корпусе), хотя скрытые торцы
                # корпуса не меняются.
                total_perimeter += facade_perimeter * qty

        visible_total = visible_per_module * qty
        premium_total = 0  # 2мм не используется по умолчанию

        # Гибрид: геометрия не ниже нормы на тип (см. EDGE_NORMS_M).
        norm = EDGE_NORMS_M.get(module.type) if EDGE_NORMS_ENABLED else None
        if norm:
            # Нормы выведены уже с запасом, поэтому перед умножением
            # на EDGE_OVERLAP делим, иначе запас начислится дважды.
            norm_visible = norm[0] * qty / EDGE_OVERLAP
            norm_total = (norm[0] + norm[1]) * qty / EDGE_OVERLAP
            visible_total = max(visible_total, norm_visible)
            total_perimeter = max(total_perimeter, norm_total)
            visible_per_module = visible_total / qty

        q.edge_total_m    += total_perimeter * EDGE_OVERLAP
        q.edge_visible_m  += visible_total * EDGE_OVERLAP
        q.edge_premium_m  += premium_total * EDGE_OVERLAP

        # ── КРОМКА МДФ (1мм) — для плитного МДФ ──
        if (
            mat_props.get("mdf_needs_edge", False)
            and module.facades
            and module.facades.get("count", 0) > 0
            and not facade_is_glass
        ):
            fc = module.facades.get("count", 1)
            fh = (module.height - 4) / 1000
            fw = (module.width / fc - 3) / 1000
            facade_perimeter = (2 * fh + 2 * fw) * fc * qty
            # Высокие фасады >2000мм → 2мм кромка.
            # Сырой метраж копится в edge_2_m и УЧИТЫВАЕТСЯ в финальном
            # распределении (edge_2_m = ceil(premium) + ceil(edge_2_m)), см. ниже.
            if module.height > 2000:
                q.edge_2_m += facade_perimeter * EDGE_OVERLAP
            else:
                q.edge_mdf_1mm_m += facade_perimeter * EDGE_OVERLAP
        else:
            q.mdf_facade_needs_edge = False

        # ── Фасады (кроме стеклянных: они считаются строкой «Стекло») ──
        if (
            module.facades
            and module.facades.get("count", 0) > 0
            and not facade_is_glass
        ):
            facade_count = module.facades.get("count", 1)
            facade_h_m = (module.height - 4) / 1000   # зазор 4мм
            facade_w_m = (module.width / facade_count - 3) / 1000  # зазор 3мм
            q.facades_area_m2 += facade_w_m * facade_h_m * facade_count * qty

        # ── Плёнка ПВХ (если фасады IVEGO) ──
        if (
            mat_props["facade_type"] == "pvh"
            and module.facades
            and module.facades.get("count", 0) > 0
            and not facade_is_glass
        ):
            facade_count = module.facades.get("count", 1)
            facade_h_m = (module.height - 4) / 1000
            facade_w_m = (module.width / facade_count - 3) / 1000
            q.pvc_film_m2 += facade_w_m * facade_h_m * facade_count * qty

        # ── P1-07: способ открывания ──
        module_handle_mode = str(
            getattr(module, "handle_mode", None) or mode or "handles"
        ).lower()
        if module_handle_mode not in {"handles", "gola", "push", "lift"}:
            module_handle_mode = "handles"
        has_doors = bool(module.facades and module.facades.get("count", 0) > 0)

        if module_handle_mode == "gola" and has_doors:
            # Профиль-ручка: горизонтальный по ширине модуля, вертикальный —
            # по высоте высоких модулей. Метраж затем переводится в штуки по 3 м.
            q.gola_horizontal_m += (module.width / 1000) * qty
            if module.type in ("penal", "column", "wardrobe", "tall_cabinet"):
                q.gola_vertical_m += (module.height / 1000) * qty
        if module_handle_mode == "lift" and has_doors:
            # Подъёмник — один на модуль (верхние базы/антресоли).
            q.lift_mechanism_count += 1 * qty

        # ── Ручки: двери + ящики (P1-04) ──
        # На раздвижные створки обычные накладные ручки не ставят — профиль-ручка
        # отдельной позицией в прайсе отсутствует (цена не выдумывается).
        module_door_system = str(
            getattr(module, "door_system", None) or rules.door_system or "hinged"
        ).lower()
        is_sliding_module = module_door_system == "sliding"

        if (
            module_handle_mode == "handles"
            and not is_sliding_module
            and module.type in HINGED_MODULE_TYPES
        ):
            if module.facades and module.facades.get("count", 0) > 0:
                fc = module.facades.get("count", 1)
                for _ in range(fc):
                    if module.height <= rules.handles_tall_threshold_mm:
                        q.handles_count += rules.handles_short_door * qty
                    else:
                        q.handles_count += rules.handles_tall_door * qty

            # Ручки ящиков считаются ВМЕСТЕ с дверными (раньше — elif, и модуль
            # «2 двери + 3 ящика» получал 2 ручки вместо 5).
            if module.drawers and module.drawers.get("count", 0) > 0:
                q.handles_count += int(module.drawers.get("count", 0)) * qty

        # ── Петли: по высоте ОДНОЙ ДВЕРИ, а не модуля ──
        # Ящики = без петель (используют боксы)
        # Встраиваемая техника = петли в комплекте, НЕ считаем
        hinges_before = q.hinges_count
        if module.type in HINGED_MODULE_TYPES:
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

            # Sliding (шкаф-купе/гардеробная, door_system="sliding"): петли НЕ
            # ставятся — раздвижные створки ездят по системе (верхняя/нижняя
            # направляющие + ролики). Раздвижная система НЕ учтена в смете:
            # готовой позиции с ценой в прайсе нет (price_manager.py) — цену
            # не выдумываем, оставляем только исключение петель.
            if (
                module_handle_mode != "lift"
                and not is_sliding_module
                and not has_drawers_only
                and not is_appliance_penal
                and module.facades
            ):
                facade_count = module.facades.get("count", 1)
                # Высота двери = высота модуля: facades.count описывает двери РЯДОМ
                # (промпт: «вертикальная линия = стык двух дверей»), каждая — полная
                # высота. Деление высоты на число дверей (как для «друг на друге»)
                # занижало петли высоких дверей. Единое правило — hinges_per_door().
                #
                # Вес фасада: для BLUM/HETTICH (высота×вес) оцениваем вес ОДНОЙ
                # двери по габаритам (ширина модуля / число дверей × высота двери)
                # и материалу фасада из mat_props (см. estimate_facade_weight_kg).
                # Для FIRMAX вес не передаём: «правила заказчика» — только по высоте.
                hinge_brand_key = (hinge_brand or DEFAULT_HINGE_BRAND).upper()
                door_weight_kg = None
                if hinge_brand_key in ("BLUM", "HETTICH"):
                    door_weight_kg = _estimate_door_weight_kg(
                        module, facade_count, mat_props
                    )
                q.hinges_count += (
                    hinges_per_door(
                        module.height,
                        brand=hinge_brand_key,
                        door_weight_kg=door_weight_kg,
                    )
                    * facade_count
                    * qty
                )
                q.hinge_brand = hinge_brand_key

        # P1-07: push-to-open — механизм на каждую петлю (цена отдельной
        # позицией; без неё строка missing и КП блокируется).
        if module_handle_mode == "push" and has_doors:
            q.push_open_count += max(0, q.hinges_count - hinges_before)

        # ── Ящики (AI + рекомендации) ──
        if module.drawers and module.drawers.get("count", 0) > 0:
            drawer_count = module.drawers.get("count", 1)
            q.drawers_count += drawer_count * qty

            # Определяем систему ящиков по ширине фасада — только если
            # оператор не выбрал модель явно (P1-06: выбор важнее эвристики).
            if not drawer_system:
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

    # ── СМЕЖНЫЕ СТЕНКИ ──
    # Подтверждено собственником 2026-11-17: каждый корпус собирается со
    # СВОИМИ боковинами, общих боковин нет. Раньше здесь вычиталось 70 %
    # площади стыков для модулей одного типа/глубины — ЛДСП в группах был
    # занижен на 5,7–10,7 %. Эвристика удалена; BOM (bom.py) всегда считал
    # каждый корпус отдельно, теперь quantity_calc с ним совпадает.

    # ── ИТОГОВЫЕ РАСЧЁТЫ ──

    # P1-17: тип фасада не выбран — НЕ добавляем площадь в ЛДСП и позже не
    # маппим в ПВХ. Раньше «unknown» трактовался как ЛДСП-фасад здесь и как
    # ПВХ в Excel: двойной учёт + неверная цена.
    if mat_props["facade_type"] == "unknown" and q.facades_area_m2 > 0:
        q.warnings.append(
            "Тип фасада не выбран: укажите МДФ-плёнку, МДФ-лак, ЛДСП или стекло"
        )
        logger.info(
            "Тип фасада unknown: %.2f м² не добавлены в ЛДСП (P1-17)",
            q.facades_area_m2,
        )

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

    # Кромка ЛДСП: распределяем.
    # КВЕРК P7-5: edge_2_m к этому моменту содержит сырой метраж 2мм-кромки
    # высоких МДФ-фасадов (>2000мм), накопленный в цикле. Ранее он безусловно
    # перезаписывался ceil(edge_premium_m)=0 (edge_premium_m не накапливается),
    # и кромка терялась из сметы. Теперь складываем оба источника 2мм:
    # премиум ЛДСП (edge_premium_m) + высокие МДФ-фасады (накоплено в edge_2_m).
    q.edge_2_m  = math.ceil(q.edge_premium_m) + math.ceil(q.edge_2_m)
    q.edge_08_m = math.ceil(q.edge_visible_m)
    q.edge_04_m = math.ceil(q.edge_total_m - q.edge_visible_m - q.edge_premium_m)
    q.edge_04_m = max(q.edge_04_m, 0)

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
    if auto_gola and rules.auto_gola and not has_spec:
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

            q.gola_vertical_m = (avg_height_m + SAW_KERF_MM / 1000) * vertical_count
            q.gola_vertical_pcs = vertical_count

        # Штуки по 3 метра
        q.gola_horizontal_pcs = math.ceil(q.gola_horizontal_m / 3) if q.gola_horizontal_m > 0 else 0

    # LED: отдельный opt-in, не зависит от GOLA (лента идёт по длине нижних баз).
    # Если spec уже добавил LED — не дублируем блоки питания/датчики.
    if auto_led and rules.auto_gola and not has_spec and q.led_strip_m == 0:
        q.led_strip_m = lower_modules_width * 0.7
        if q.led_strip_m > 0:
            led_watt = q.led_strip_m * 9.6 * 1.2  # мощность с запасом 20%
            if q.led_power_supply == 0:
                q.led_power_supply = max(1, math.ceil(led_watt / 100))
            if q.led_sensor == 0:
                q.led_sensor = 1

    # ── P1-07: GOLA/push/lift — без накладных ручек ──
    if q.handle_mode != "handles":
        q.handles_count = 0
    # GOLA из явного режима: метраж → штуки по 3 м (если авто-GOLA не считала).
    if q.gola_horizontal_m > 0 and q.gola_horizontal_pcs == 0:
        q.gola_horizontal_pcs = math.ceil(q.gola_horizontal_m / 3)
    if q.gola_vertical_m > 0 and q.gola_vertical_pcs == 0:
        q.gola_vertical_pcs = math.ceil(q.gola_vertical_m / 3)
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
            q.suggestions.append("📚 Рекомендация: проверить количество полок в открытых модулях")

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
    modules: list[RecognizedModule],
    room_name: str,
    materials: list[str],
    *,
    zone_type: str | None = None,
) -> list[tuple[str, str, str, float, float]]:
    """
    Заполнить позиции шаблона для одного помещения.

    Returns:
        Список кортежей: (категория, наименование, цвет/поставщик, цена, количество)
    """
    q = calculate_quantities(modules, room_name, materials, zone_type=zone_type)
    items = []

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
    # Только позиции, которые есть в прайсе/Excel: конфирмат, опоры, подвесы,
    # цоколь. Саморезы/гвозди не выдумываем: если их нет в прайсе — не считаем.
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
