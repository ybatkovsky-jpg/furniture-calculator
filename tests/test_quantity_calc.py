"""
Тесты для quantity_calc — калькулятора количеств материалов (v3.0).

Покрывают detect_material_properties (единая классификация материалов),
calculate_quantities (ядро: листы ЛДСП/МДФ/ХДФ, кромка, фасады, петли,
ручки, ящики, столешница, крепёж, Gola/LED, авто-комплектующие, штанги,
смежные стенки) и fill_template_for_room (позиции сметы для Excel).

Стиль — как в test_edge_calc/test_fastener_calc: стабы модулей через
SimpleNamespace (quantity_calc читает только атрибуты type/width/depth/
height/quantity/shelves/facades/drawers/is_corner), регрессионный
бейзлайн текущего поведения, включая зафиксированные кверки (см.
ЗАМЕЧАНИЯ_ТЕСТИРОВАНИЯ.md, P7).
"""

import math
from types import SimpleNamespace

import pytest

from app.services.quantity_calc import (
    MaterialQuantities,
    calculate_quantities,
    detect_material_properties,
    fill_template_for_room,
)

# Листы: EGGER 2750×1830=5.0325 м²; default 2800×2070=5.796 м²; МДФ standard 3.416
EGGER_SHEET_M2 = 5.0325
DEFAULT_SHEET_M2 = 5.796
HDF_SHEET_M2 = 3.416


def _mk(t, w, d=560, h=820, q=1, s=0, fc=None, dr=None, corner=False):
    """Стаб RecognizedModule: только атрибуты, которые читает quantity_calc."""
    facades = {"count": fc} if fc is not None else None
    drawers = {"count": dr} if dr is not None else None
    return SimpleNamespace(
        type=t, width=w, depth=d, height=h, quantity=q, shelves=s,
        facades=facades, drawers=drawers, is_corner=corner,
    )


def _calc(modules, materials, room="Кухня", **kw):
    """calculate_quantities с дефолтными отключёнными авто-дополнениями,
    чтобы тесты ядра не зависели от рекомендаций (включаются точечно)."""
    kw.setdefault("auto_accessories", False)
    kw.setdefault("auto_drawers", False)
    kw.setdefault("auto_led", False)
    return calculate_quantities(modules, room, materials, **kw)


# ════════════════════════════════════════════════════════════════════
# detect_material_properties
# ════════════════════════════════════════════════════════════════════

def test_props_empty():
    """КВЕРК P7-6: при пустом вводе ранний return не добавляет ключ mdf_needs_edge
    (все вызывающие используют .get(), поэтому не ломается) — фиксируем форму."""
    p = detect_material_properties([])
    assert p == {
        "surface": "plain", "brand": "unknown", "facade_type": "unknown",
        "has_glass": False,
    }
    assert "mdf_needs_edge" not in p


@pytest.mark.parametrize("mats,brand", [
    (["EGGER U702 ST9"], "EGGER"),
    (["EXTRAVERT A104 ST9"], "EXTRAVERT"),
    (["LAMARTY 1001"], "LAMARTY"),
    (["ТОМЛЕСДРЕВ 401"], "ТОМЛЕСДРЕВ"),
    (["EMDIWAY U702"], "unknown"),      # EMDIWAY — бренд фасадов, не ЛДСП
    (["какой-то декор"], "unknown"),
])
def test_props_brand(mats, brand):
    assert detect_material_properties(mats)["brand"] == brand


@pytest.mark.parametrize("mats", [
    ["EGGER H3146 ST19 Дуб Лоренцо"],        # древесный декор H3xxx
    ["EGGER H1379 ST36 Дуб Орлеанский"],
    ["ДУБ"], ["ОРЕХ"], ["текстурный ЛДСП"],  # keyword-триггеры
    ["КАМЕНЬ"], ["МРАМОР"], ["БЕТОН"],
])
def test_props_texture_detected(mats):
    assert detect_material_properties(mats)["surface"] == "texture"


@pytest.mark.parametrize("mats", [
    ["EGGER U702 ST9"], ["Белый глянец"], ["ПВХ плёнка белая"],
])
def test_props_plain_not_texture(mats):
    assert detect_material_properties(mats)["surface"] == "plain"


@pytest.mark.parametrize("mats,facade_type", [
    (["EMDIWAY U702 ST9"], "emdiway"),
    (["EMDIWAY Titan U960"], "emdiway_titan"),
    (["МДФ Лакокраска матовая"], "paint_matte"),
    (["МАТОВЫЙ МДФ"], "paint_matte"),
    (["МДФ глянец"], "paint_gloss"),
    (["ПВХ плёнка IVEGO"], "pvh"),
    (["EGGER U702"], "unknown"),            # ЛДСП без фасадного типа
])
def test_props_facade_type(mats, facade_type):
    assert detect_material_properties(mats)["facade_type"] == facade_type


@pytest.mark.parametrize("mats", [
    ["стекло"], ["ЗЕРКАЛО"], ["витрина с ВИТРИН"], ["glass"],
])
def test_props_glass(mats):
    assert detect_material_properties(mats)["has_glass"] is True


def test_props_no_glass():
    assert detect_material_properties(["EGGER U702"])["has_glass"] is False


@pytest.mark.parametrize("mats", [
    ["EMDIWAY U702"], ["EVOGLOSS белый"], ["AGT 801"], ["EGGER PERFECT SENSE"],
])
def test_props_plated_mdf_needs_edge(mats):
    """Плитный МДФ (EMDIWAY/EVOGLOSS/AGT/Perfect Sense) — торец открыт, нужна кромка 1мм."""
    assert detect_material_properties(mats)["mdf_needs_edge"] is True


def test_props_painted_mdf_no_edge():
    """Плёнка/краска закрывает торец — кромка МДФ не нужна."""
    assert detect_material_properties(["МДФ Лакокраска"])["mdf_needs_edge"] is False
    assert detect_material_properties(["ПВХ плёнка"])["mdf_needs_edge"] is False


def test_props_real_album_material():
    """Реальный материал с альбома (стр.0001): EGGER + древесный декор."""
    p = detect_material_properties(["Egger H1379 ST36 Дуб Орлеанский коричневый", "NCS S 7020 R90B"])
    assert p["brand"] == "EGGER"
    assert p["surface"] == "texture"


# ════════════════════════════════════════════════════════════════════
# calculate_quantities: пустой ввод и базовые случаи
# ════════════════════════════════════════════════════════════════════

def test_empty_modules_zero_quantities():
    q = _calc([], [])
    assert q.ldsp_area_m2 == 0 and q.ldsp_sheets == 0
    assert q.facades_area_m2 == 0 and q.mdf_sheets == 0
    assert q.hdf_sheets == 0 and q.pvc_film_m2 == 0
    assert q.edge_total_m == 0 and q.edge_08_m == 0 and q.edge_04_m == 0
    assert q.hinges_count == 0 and q.handles_count == 0
    assert q.drawers_count == 0 and q.confirmat_count == 0
    assert q.adjustable_feet == 0 and q.wall_mounts == 0
    assert q.countertop_length_m == 0 and q.plinth_strips == 0
    assert q.gola_horizontal_pcs == 0 and q.led_strip_m == 0


def test_kitchen_single_lower_core():
    """Один нижний модуль 600×560×820, EGGER U702 однотон, авто off.

    ЛДСП: боковины 2×0.564×0.824 + дно/крыша 2×0.604×0.564 + полка 0.604×0.564
         = 1.95144 м²; ХДФ 0.6×0.82=0.492; кромка всего периметра ×1.3.
    """
    q = _calc([_mk("lower_base", 600)], ["EGGER U702 ST9"], zone_type="kitchen")
    assert q.ldsp_brand == "EGGER"
    assert q.ldsp_area_m2 == pytest.approx(1.95144, abs=1e-4)
    assert q.ldsp_sheets == math.ceil(1.95144 * 1.30 / EGGER_SHEET_M2) == 1
    # ХДФ: ceil(0.492*1.3/3.416)=1 (мин. 1 лист)
    assert q.hdf_sheets == 1
    # Кромка: visible 4.498 → 5; 04 = 20.02 − 4.498 → 16 (смотри probe)
    assert q.edge_visible_m == pytest.approx(4.498, abs=1e-3)
    assert q.edge_08_m == 5 and q.edge_04_m == 16
    # Модуль без фасадов → петель/ручек нет
    assert q.hinges_count == 0 and q.handles_count == 0
    # Столешница (кухня): (0.6+0)×1.1; глубина 640
    assert q.countertop_length_m == pytest.approx(0.66)
    assert q.countertop_depth_mm == 640
    # Крепёж: 1 крышка+1 дно+1 полка → (6+6+4)*1.1 → 18
    assert q.confirmat_count == 18
    assert q.adjustable_feet == 4
    assert q.plinth_strips == 1
    # Gola: горизонтальный 0.6 м → 1 шт; вертикальный 2 шт
    assert q.gola_horizontal_m == pytest.approx(0.6)
    assert q.gola_horizontal_pcs == 1
    assert q.gola_vertical_pcs == 2


def test_default_sheet_when_brand_unknown():
    """Без бренда в материалах — лист default 2800×2070 (5.796 м²)."""
    q = _calc([_mk("lower_base", 600)], [], zone_type="kitchen")
    assert q.ldsp_brand == ""
    assert q.ldsp_sheets == math.ceil(1.95144 * 1.3 / DEFAULT_SHEET_M2) == 1


def test_texture_material_label_and_waste():
    """Древесный декор → 'текстура'; запас тот же 1.30 (лист всё равно 1)."""
    q = _calc([_mk("lower_base", 600)], ["EGGER H3146 ST19 Дуб Лоренцо"], zone_type="kitchen")
    assert q.ldsp_material == "текстура"
    assert q.ldsp_area_m2 == pytest.approx(1.95144)
    assert q.ldsp_sheets == 1


# ════════════════════════════════════════════════════════════════════
# Фасады: площади, петли, ручки, перегородки
# ════════════════════════════════════════════════════════════════════

def test_hinges_and_facade_area_single_door():
    """Одна дверца 820 мм: 2 петли FIRMAX; фасад 597×816 мм (зазоры 3/4 мм)."""
    q = _calc([_mk("lower_base", 600, fc=1)], ["EGGER U702"], zone_type="kitchen")
    assert q.facades_area_m2 == pytest.approx(0.597 * 0.816, abs=1e-4)
    assert q.hinges_count == 2
    # Кухня с Gola → ручки обнуляются (Gola закрывает открытые торцы)
    assert q.handles_count == 0


def test_hinges_multi_door_per_module():
    """Широкий низ 1200 с 2 дверцами рядом: 4 петли, перегородка 1 (запас в конфирматах)."""
    q = _calc([_mk("lower_base", 1200, fc=2)], ["EGGER U702"], zone_type="kitchen")
    assert q.hinges_count == 4          # 2 двери × 2 петли
    # фасад: (1200/2-3)=597 мм × 816 мм × 2 двери
    assert q.facades_area_m2 == pytest.approx(0.597 * 0.816 * 2, abs=1e-4)
    # конфирматы: (1 полка×4 + крышка 6 + дно 6 + перегородка 4) = 20 → ceil(22)
    assert q.confirmat_count == 22
    assert q.adjustable_feet == 6       # ширина > 900 → 6 опор


def test_penal_facade_hinges_by_height():
    """Высокий пенал с фасадом: петли по высоте двери (2100 → 4)."""
    q = _calc([_mk("penal", 600, h=2100, fc=1)], ["EGGER U702"], zone_type="kitchen")
    assert q.hinges_count == 4


def test_penal_appliance_no_hinges():
    """Пенал 600+ без фасадов — под технику: петли в комплекте, не считаем."""
    q = _calc([_mk("penal", 600, h=2100)], ["EGGER U702"], zone_type="kitchen")
    assert q.hinges_count == 0


def test_facades_from_ldsp_go_into_ldsp():
    """Фасады из ЛДСП (facade_type unknown) добавляются к ЛДСП с +15%."""
    q = _calc([_mk("lower_base", 600, fc=1)], ["EGGER U702"], zone_type="kitchen")
    # 1.95144 + 0.487152×1.15 = 2.5117
    assert q.ldsp_area_m2 == pytest.approx(1.95144 + 0.597 * 0.816 * 1.15, abs=1e-4)


def test_mdf_facades_use_mdf_sheets():
    """EMDIWAY: фасады считаются отдельно, лист МДФ standard (площадь <3 м²)."""
    q = _calc([_mk("penal", 600, h=1500, fc=1)], ["EMDIWAY U702"], zone_type="kitchen")
    assert q.facade_type is None or q.facades_area_m2 > 0
    area = (0.597) * (1500 - 4) / 1000
    assert q.facades_area_m2 == pytest.approx(area, abs=1e-4)
    assert q.mdf_sheets == math.ceil(area * 1.15 / 3.416) == 1


def test_mdf_edge_1mm_for_plated_mdf():
    """Плитный МДФ (EMDIWAY), фасад 1500 мм → кромка 1 мм МДФ = периметр фасада ×1.3."""
    q = _calc([_mk("penal", 600, h=1500, fc=1)], ["EMDIWAY U702"], zone_type="kitchen")
    assert q.mdf_facade_needs_edge is True
    fh, fw = (1500 - 4) / 1000, (600 - 3) / 1000
    assert q.edge_mdf_1mm_m == math.ceil((2 * fh + 2 * fw) * 1.30) == 6


def test_pvc_film_m2_with_10pct_margin():
    """ПВХ-фасады: плёнка IVEGO = площадь фасадов ×1.10 (округление до 0.1)."""
    q = _calc([_mk("lower_base", 600, fc=1)], ["ПВХ плёнка IVEGO"], zone_type="kitchen")
    assert q.facades_area_m2 == pytest.approx(0.597 * 0.816, abs=1e-4)
    assert q.pvc_film_m2 == pytest.approx(round(0.597 * 0.816 * 1.10, 1), abs=0.01)


def test_glass_detection_in_material_props():
    p = detect_material_properties(["EGGER U702", "стекло"])
    assert p["has_glass"] is True


# ════════════════════════════════════════════════════════════════════
# Ящики: количество и система по ширине фасада
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("width,system", [
    (1200, "Legrabox"),      # > 900
    (800, "Tandembox"),      # 600..900
    (400, "Boyard Start"),   # <= 600
])
def test_drawer_system_by_width(width, system):
    q = _calc([_mk("lower_base", width, fc=1, dr=2)], ["EGGER U702"], zone_type="kitchen")
    assert q.drawers_count == 2
    assert q.drawer_system == system


def test_drawers_only_module_no_hinges():
    """Модуль с ящиками и без фасадов: петли не нужны (боксы вместо петель)."""
    q = _calc([_mk("lower_base", 400, dr=3)], ["EGGER U702"], zone_type="kitchen")
    assert q.drawers_count == 3
    assert q.hinges_count == 0


def test_drawers_with_facades_still_hinged():
    """Ящики + фасад (фасад-фальшпанель над ящиками) — петли считаются."""
    q = _calc([_mk("lower_base", 400, fc=1, dr=3)], ["EGGER U702"], zone_type="kitchen")
    assert q.drawers_count == 3
    assert q.hinges_count == 2


# ════════════════════════════════════════════════════════════════════
# Количество модулей > 1 и смежные стенки
# ════════════════════════════════════════════════════════════════════

def test_quantity_multiplier_scales_quantities():
    """q=3: площади/петли/опоры ×3; столешница учитывает 3 модуля (1.8+0.05×2)×1.1."""
    q = _calc([_mk("lower_base", 600, q=3)], ["EGGER U702"], zone_type="kitchen")
    # Экономия 1 смежной стенки: 3×1.95144 − 0.464736 = 5.3896
    assert q.ldsp_area_m2 == pytest.approx(3 * 1.95144 - 0.564 * 0.824, abs=1e-4)
    assert q.ldsp_sheets == math.ceil(5.389584 * 1.3 / EGGER_SHEET_M2) == 2
    assert q.adjustable_feet == 12
    assert q.countertop_length_m == pytest.approx((1.8 + 2 * 0.05) * 1.1, abs=1e-4)
    assert q.gola_horizontal_m == pytest.approx(1.8, abs=1e-4)


def test_shared_sidewalls_saving():
    """4 одинаковых модуля: 70% из 3 стыков (2 шт.) экономят 2×0.464736 м² ЛДСП."""
    q = _calc([_mk("lower_base", 600) for _ in range(4)], ["EGGER U702"], zone_type="kitchen")
    expected = 4 * 1.95144 - 2 * (0.564 * 0.824)
    assert q.ldsp_area_m2 == pytest.approx(expected, abs=1e-4)
    # листы: ceil(6.8763×1.3/5.0325)=2
    assert q.ldsp_sheets == 2


def test_shared_sides_only_same_type_and_depth():
    """Модули разной глубины не делят стенки (нет экономии).

    Площади: 600×560: 1.95144 м²; 600×500: sides 2×0.504×0.824 + дно/крыша
    2×0.604×0.504 + полка 0.604×0.504 = 1.74384 м²."""
    mods = [_mk("lower_base", 600, d=560), _mk("lower_base", 600, d=500)]
    q = _calc(mods, ["EGGER U702"], zone_type="kitchen")
    assert q.ldsp_area_m2 == pytest.approx(1.95144 + 1.74384, abs=1e-4)


# ════════════════════════════════════════════════════════════════════
# Авто-комплектующие для кухни (по умолчанию)
# ════════════════════════════════════════════════════════════════════

def test_kitchen_auto_accessories_defaults():
    """auto_accessories=True (дефолт): лоток, сушка, мойка; авто-ящики 2+1 внутренний."""
    q = calculate_quantities([_mk("lower_base", 600, fc=1)], "Кухня", ["EGGER U702"],
                             zone_type="kitchen", auto_led=False)
    assert q.drawers_count == 2
    assert q.drawers_internal_count == 1
    assert q.drawer_system == "Tandembox"
    assert q.cutlery_tray_count == 1
    assert q.has_sink is True
    assert q.drying_rack_count == 1
    # бутылочница только при узком модуле <=200
    assert q.bottle_holder_count == 0


def test_kitchen_auto_bottle_holder_for_narrow_module():
    q = calculate_quantities(
        [_mk("lower_base", 150, fc=1), _mk("lower_base", 600, fc=1)],
        "Кухня", ["EGGER U702"], zone_type="kitchen", auto_led=False)
    assert q.bottle_holder_count == 1
    assert q.bottle_holder_type == "flora"


def test_kitchen_auto_led():
    """Авто-LED для кухни: лента 70% длины Gola, БП и датчик."""
    q = calculate_quantities([_mk("lower_base", 600, fc=1)], "Кухня", ["EGGER U702"],
                             zone_type="kitchen", auto_accessories=False, auto_drawers=False)
    assert q.led_strip_m == pytest.approx(0.6 * 0.7, abs=1e-4)
    assert q.led_power_supply == 1
    assert q.led_sensor == 1


def test_auto_accessories_off_when_has_spec():
    """has_spec=True: не дублируем авто-комплектующие (их добавил spec.yaml)."""
    q = calculate_quantities([_mk("lower_base", 600, fc=1)], "Кухня", ["EGGER U702"],
                             zone_type="kitchen", has_spec=True, auto_led=False)
    assert q.drawers_count == 0
    assert q.cutlery_tray_count == 0
    assert q.has_sink is False


def test_non_kitchen_no_auto_accessories():
    """Спальня (default family): без авто-ящиков, сушки, столешницы."""
    q = _calc([_mk("lower_base", 600, fc=1)], ["EGGER U702"], room="Спальня",
              zone_type="bedroom")
    assert q.drawers_count == 0
    assert q.cutlery_tray_count == 0
    assert q.countertop_length_m == 0
    assert q.gola_horizontal_pcs == 0 and q.gola_vertical_pcs == 0
    # но петли и ручки обычные (без Gola)
    assert q.hinges_count == 2
    assert q.handles_count == 1


# ════════════════════════════════════════════════════════════════════
# Gola: остров/угол добавляют вертикальные профили
# ════════════════════════════════════════════════════════════════════

def test_gola_vertical_basic_two_profiles():
    q = _calc([_mk("lower_base", 600, fc=1)], ["EGGER U702"], zone_type="kitchen")
    # средняя высота модулей с фасадами: 0.82 м (без припуска на пропил) → 2 профиля
    assert q.gola_vertical_pcs == 2
    assert q.gola_vertical_m == pytest.approx(0.82 * 2, abs=1e-4)


def test_gola_vertical_island_adds_two():
    """'остров' в имени комнаты → ещё 2 вертикальных профиля."""
    q = _calc([_mk("lower_base", 600, fc=1)], ["EGGER U702"],
              room="Кухня с островом", zone_type="kitchen")
    assert q.gola_vertical_pcs == 4
    assert q.gola_vertical_m == pytest.approx(0.82 * 4, abs=1e-4)


def test_gola_vertical_corner_adds_one():
    q = _calc([_mk("lower_base", 600, fc=1), _mk("lower_base", 600, fc=1, corner=True)],
              ["EGGER U702"], room="Кухня", zone_type="kitchen")
    assert q.gola_vertical_pcs == 3


# ════════════════════════════════════════════════════════════════════
# Гардеробные: штанги
# ════════════════════════════════════════════════════════════════════

def test_wardrobe_rods():
    """Гардеробная: штанги для модулей шириной ≥450 мм.

    КВЕРК sliding: зона «wardrobe» помечена door_system="sliding"
    (furniture_defaults) — фасады здесь это раздвижные створки, поэтому
    петель и накладных ручек НЕТ (раньше тест фиксировал ошибочные 4 петли
    на пенал 2500). Раздвижная система направляющих/роликов в прайсе
    отсутствует — в смету пока не попадает (см. комментарии в quantity_calc)."""
    q = _calc([_mk("penal", 1000, h=2500, fc=1)], [], room="Гардеробная",
              zone_type="wardrobe")
    assert q.rods_rectangular_count == 1
    assert q.hinges_count == 0          # sliding: створка без петель
    assert q.handles_count == 0         # sliding: без накладных ручек
    assert q.countertop_length_m == 0   # столешницы нет


# ════════════════════════════════════════════════════════════════════
# Раздвижные двери (door_system="sliding"): петли и ручки НЕ считаются
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("zone", ["Спальня", "Гардеробная", "wardrobe"])
def test_sliding_zone_no_hinges_no_handles(zone):
    """КВЕРК sliding: зоны с door_system="sliding" (шкаф-купе/гардеробная) —
    модули с фасадами-створками получают 0 петель и 0 накладных ручек:
    на раздвижные створки ставят систему направляющих/роликов, а не петли.
    (До фикса корпус 1800×2500 с 2 створками давал бы 8 петель и 4 ручки.)"""
    q = _calc([_mk("penal", 1800, h=2500, fc=2)], ["EGGER U702"],
              room=zone, zone_type=zone)
    assert q.facades_area_m2 > 0        # фасады-створки в смете остаются
    assert q.hinges_count == 0
    assert q.handles_count == 0


def test_sliding_vs_hinged_same_module():
    """Контраст: один и тот же корпус 1800×2500 с 2 фасадами —
    «Спальня» (sliding) → 0 петель / 0 ручек;
    «Гостиная» (hinged, без Gola) → 8 петель (2×4) / 4 ручки (2×2)."""
    mods = [_mk("penal", 1800, h=2500, fc=2)]
    q_sliding = _calc(mods, ["EGGER U702"], room="Спальня", zone_type="Спальня")
    q_hinged = _calc(mods, ["EGGER U702"], room="Гостиная", zone_type="Гостиная")
    assert (q_sliding.hinges_count, q_sliding.handles_count) == (0, 0)
    assert (q_hinged.hinges_count, q_hinged.handles_count) == (8, 4)


def test_sliding_zone_kitchen_hinged_still_counts():
    """Те же створки при hinged zone_type='Кухня': петли как раньше (8 шт),
    ручки обнуляются Gola-блоком — прежнее кухонное поведение не тронуто."""
    q = _calc([_mk("penal", 1800, h=2500, fc=2)], ["EGGER U702"],
              room="Кухня", zone_type="Кухня")
    assert q.hinges_count == 8          # 2 створки × 4 петли (2500 ≥ 2000, FIRMAX)
    assert q.handles_count == 0         # кухня с Gola — ручки не нужны (как было)


# ════════════════════════════════════════════════════════════════════
# Кромка: зафиксированные кверки (регрессионный бейзлайн, см. P7)
# ════════════════════════════════════════════════════════════════════

def test_mdf_tall_facade_gets_2mm_edge():
    """КВЕРК P7-5 (исправлено): фасад МДФ >2000 мм получает 2мм-кромку ≈ периметру.

    Раньше накопленный в edge_2_m метраж безусловно перезаписывался
    ceil(edge_premium_m)=0 (edge_premium_m нигде не накапливается), и высокий
    МДФ-фасад попадал в смету БЕЗ кромки (ни 2мм, ни 1мм). Теперь финальное
    распределение складывает оба источника 2мм: премиум ЛДСП + МДФ-фасад."""
    q = _calc([_mk("penal", 600, h=2500, fc=1)], ["EMDIWAY U702"], zone_type="kitchen")
    assert q.mdf_facade_needs_edge is True
    fh, fw = (2500 - 4) / 1000, (600 - 3) / 1000
    # периметр фасада × запас 1.30 → ceil = 9 м.п. кромки 2мм
    assert q.edge_2_m == math.ceil((2 * fh + 2 * fw) * 1.30) == 9
    assert q.edge_mdf_1mm_m == 0        # в 1мм-кромку не попадает (ветка >2000)


def test_mdf_tall_and_short_facades_separate_buckets():
    """Высокий (2мм) и короткий (1мм) МДФ-фасады в одном проекте не смешиваются."""
    q = _calc(
        [_mk("penal", 600, h=2500, fc=1), _mk("penal", 600, h=1500, fc=1)],
        ["EMDIWAY U702"], zone_type="kitchen")
    assert q.edge_2_m == 9          # только высокий фасад → 2мм
    assert q.edge_mdf_1mm_m == 6    # только короткий фасад → 1мм


def test_tall_ldsp_facade_not_mistaken_for_mdf_2mm():
    """Высокий НЕ-МДФ фасад (ЛДСП EGGER) не получает 2мм кромку МДФ:
    фикс применяется только к плитному МДФ >2000мм (mdf_needs_edge)."""
    q = _calc([_mk("penal", 600, h=2500, fc=1)], ["EGGER U702"], zone_type="kitchen")
    assert q.mdf_facade_needs_edge is False
    assert q.edge_2_m == 0
    assert q.edge_mdf_1mm_m == 0


def test_mdf_1mm_edge_not_added_to_ldsp_edge():
    """Кромка МДФ (1мм) не смешивается с кромкой ЛДСП (0.4/0.8/2)."""
    q = _calc([_mk("penal", 600, h=1500, fc=1)], ["EMDIWAY U702"], zone_type="kitchen")
    assert q.edge_mdf_1mm_m == 6
    assert q.edge_04_m == 12            # без вклада МДФ-фасада


# ════════════════════════════════════════════════════════════════════
# fill_template_for_room — позиции сметы
# ════════════════════════════════════════════════════════════════════

def test_fill_template_empty_non_kitchen():
    assert fill_template_for_room([], "Гостиная", []) == []


def test_fill_template_kitchen_structure():
    items = fill_template_for_room(
        [_mk("lower_base", 600, fc=1)], "Кухня", ["EGGER U702"], zone_type="kitchen")
    assert items and all(len(i) == 5 for i in items)
    cats = {i[0] for i in items}
    assert {"ЛДСП", "КРОМКА", "ХДФ", "GOLA", "СТОЛЕШНИЦА", "КРЕПЁЖ"} <= cats
    # ЛДСП: 1 лист по 6000 (однотон)
    ldsp = next(i for i in items if i[0] == "ЛДСП")
    assert ldsp[4] == 1 and ldsp[3] == 6000
    # Кромка EGGER 0.8: видимые торцы (корпус+полка+фасад) ×1.3 → 9 м.п.
    edge08 = next(i for i in items if i[1] == "EGGER 0,8*19")
    assert edge08[4] == 9


def test_fill_template_emdiway_rows():
    """EMDIWAY Titan → фасады по 11200 ₽/м² и кромка МДФ 1*22 отдельной строкой."""
    items = fill_template_for_room(
        [_mk("penal", 600, h=1500, fc=1)], "Кухня", ["EMDIWAY Titan"], zone_type="kitchen")
    facades = next(i for i in items if i[0] == "ФАСАДЫ")
    assert facades[1] == "EMDIWAY Titan"
    assert facades[3] == 11200
    edge_mdf = next((i for i in items if i[0] == "КРОМКА МДФ"), None)
    assert edge_mdf is not None and edge_mdf[1] == "Кромка МДФ 1*22"


def test_fill_template_pvc_rows():
    """ПВХ-фасады → позиция «ПЛЁНКА ПВХ»."""
    items = fill_template_for_room(
        [_mk("lower_base", 600, fc=1)], "Кухня", ["ПВХ плёнка IVEGO"], zone_type="kitchen")
    assert any(i[0] == "ПЛЁНКА ПВХ" for i in items)


def test_material_quantities_dataclass_defaults():
    q = MaterialQuantities()
    assert q.ldsp_sheets == 0 and q.hinges_count == 0
    assert q.drawer_system == "Tandembox"
    assert q.hinge_brand == "FIRMAX"
    assert q.suggestions == [] and q.warnings == []
