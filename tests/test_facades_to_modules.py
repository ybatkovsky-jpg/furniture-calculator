"""
Тесты для преобразования фасадов в модули мебели.

Покрывают:
- GeminiImageAnalyzer._facades_to_modules — основной метод конвейера
  (grouped facades → RecognizedModule с quantity)
- facades_to_modules (module-level) — конвертация FacadeData → RecognizedModule

Фиксируют текущее поведение (включая известные баги) как регрессионный базлайн.
Чистая Python-логика, без вызова моделей.
"""

import pytest

from app.services.image_analyzer import (
    GeminiImageAnalyzer,
    RecognizedModule,
    FacadeData,
    facades_to_modules,
)
from app.services.scale_calc import ScaledFacade


def make_facade(zone, width_mm, height_mm=716, idx_x_pct=0, idx_w_pct=20):
    """Хелпер: создать ScaledFacade с нужными параметрами."""
    return ScaledFacade(
        zone=zone,
        width_mm=width_mm,
        height_mm=height_mm,
        bbox_x_pct=idx_x_pct,
        bbox_w_pct=idx_w_pct,
    )


def make_analyzer():
    """Создать анализатор для доступа к методу _facades_to_modules."""
    return GeminiImageAnalyzer()


# ════════════════════════════════════════════════════════════════════
# ПУСТОЙ ВВОД И ЗНАЧЕНИЯ ПО УМОЛЧАНИЮ
# ════════════════════════════════════════════════════════════════════

def test_empty_input_returns_empty():
    """Пустой список фасадов → пустой список модулей."""
    analyzer = make_analyzer()
    assert analyzer._facades_to_modules([]) == []


def test_none_glass_and_drawer_become_empty_sets():
    """None для glass_indices/drawer_indices безопасно заменяется на set()."""
    analyzer = make_analyzer()
    facades = [make_facade("lower", 600)]
    # Не должно падать с TypeError на 'None in set'
    result = analyzer._facades_to_modules(facades, glass_indices=None, drawer_indices=None)
    assert len(result) == 1
    assert result[0].has_glass is False
    assert result[0].drawers is None


# ════════════════════════════════════════════════════════════════════
# УГЛОВОЙ МОДУЛЬ (только по явному is_corner от модели; авто-эвристика
# «ширина 800-1100 в зоне lower = угол» УБРАНА как источник ложных углов:
# прямые тумбы/шкафы с дверями ~900мм превращались в «угловые»)
# ════════════════════════════════════════════════════════════════════

def test_corner_requires_explicit_flag_950():
    """Ширина 950 в lower БЕЗ флага is_corner → НЕ угол, обычная lower_base."""
    analyzer = make_analyzer()
    facades = [make_facade("lower", 950)]
    result = analyzer._facades_to_modules(facades)
    assert result[0].type == "lower_base"
    assert result[0].is_corner is False
    assert result[0].depth == 560  # обычная глубина, не квадрат


def test_corner_explicit_flag_950():
    """Ширина 950 в lower С флагом is_corner=True → corner (квадратный)."""
    analyzer = make_analyzer()
    facades = [ScaledFacade(zone="lower", width_mm=950, height_mm=820,
                            bbox_x_pct=0, bbox_w_pct=20, is_corner=True)]
    result = analyzer._facades_to_modules(facades)
    assert result[0].type == "corner"
    assert result[0].is_corner is True
    assert result[0].width == 950
    assert result[0].depth == 950  # квадратный
    assert result[0].quantity == 1


def test_width_800_no_flag_not_corner():
    """Ровно 800мм в lower БЕЗ флага → НЕ угол (эвристика убрана)."""
    analyzer = make_analyzer()
    result = analyzer._facades_to_modules([make_facade("lower", 800)])
    assert result[0].type == "lower_base"


def test_width_1100_no_flag_not_corner():
    """Ровно 1100мм в lower БЕЗ флага → НЕ угол (эвристика убрана)."""
    analyzer = make_analyzer()
    result = analyzer._facades_to_modules([make_facade("lower", 1100)])
    assert result[0].type == "lower_base"


def test_below_corner_range_not_corner():
    """799мм в lower БЕЗ флага → НЕ угол."""
    analyzer = make_analyzer()
    result = analyzer._facades_to_modules([make_facade("lower", 799)])
    assert result[0].type == "lower_base"


def test_above_corner_range_not_corner():
    """1101мм в lower → НЕ угол (верхняя граница 1100)."""
    analyzer = make_analyzer()
    result = analyzer._facades_to_modules([make_facade("lower", 1101)])
    assert result[0].type == "lower_base"


def test_corner_in_upper_zone_not_detected():
    """Ширина 950 в зоне upper БЕЗ флага is_corner → НЕ угол (эвристика только lower)."""
    analyzer = make_analyzer()
    result = analyzer._facades_to_modules([make_facade("upper", 950)])
    assert result[0].type == "upper_base"
    assert result[0].is_corner is False


def test_corner_in_upper_zone_with_flag():
    """Угловой фасад в upper-зоне С флагом is_corner=True → распознаётся как corner.

    После фикса B2: ScaledFacade.is_corner пробрасывается из ответа модели,
    поэтому угол в upper-зоне (которого эвристика ширины не ловила) теперь
    распознаётся через явный флаг.
    """
    analyzer = make_analyzer()
    facades = [ScaledFacade(
        zone="upper", width_mm=950, height_mm=720,
        bbox_x_pct=0, bbox_w_pct=20, is_corner=True,
    )]
    result = analyzer._facades_to_modules(facades)
    assert result[0].type == "corner"
    assert result[0].is_corner is True
    assert result[0].depth == 950  # квадратный


def test_is_corner_flag_overrides_width_heuristic():
    """Явный флаг is_corner=True срабатывает даже для нестандартной ширины."""
    analyzer = make_analyzer()
    # Ширина 500 (вне диапазона 800-1100 эвристики), но флаг=True
    facades = [ScaledFacade(
        zone="lower", width_mm=500, height_mm=820,
        bbox_x_pct=0, bbox_w_pct=10, is_corner=True,
    )]
    result = analyzer._facades_to_modules(facades)
    assert result[0].type == "corner"
    assert result[0].is_corner is True


# ════════════════════════════════════════════════════════════════════
# ПЕНАЛ
# ════════════════════════════════════════════════════════════════════

def test_penal_always_separate_module():
    """Пенал → всегда отдельный модуль (не группируется), quantity=1."""
    analyzer = make_analyzer()
    # 3 пенала одинаковой ширины — должны дать 3 отдельных модуля
    facades = [
        make_facade("penal", 600, height_mm=2500),
        make_facade("penal", 600, height_mm=2500),
        make_facade("penal", 600, height_mm=2500),
    ]
    result = analyzer._facades_to_modules(facades)
    assert len(result) == 3
    assert all(m.type == "penal" for m in result)
    assert all(m.quantity == 1 for m in result)


def test_penal_depth_uses_lower_default():
    """Глубина пенала = default_depth_lower из FurnitureRules (560 по умолчанию)."""
    analyzer = make_analyzer()
    result = analyzer._facades_to_modules([make_facade("penal", 600, height_mm=2500)])
    assert result[0].depth == 560  # default FurnitureRules


# ════════════════════════════════════════════════════════════════════
# ГРУППИРОВКА ПО ШИРИНЕ (±30мм)
# ════════════════════════════════════════════════════════════════════

def test_grouping_same_width():
    """3 дверцы 600мм в lower → 1 модуль quantity=3."""
    analyzer = make_analyzer()
    facades = [
        make_facade("lower", 600),
        make_facade("lower", 600),
        make_facade("lower", 600),
    ]
    result = analyzer._facades_to_modules(facades)
    assert len(result) == 1
    assert result[0].type == "lower_base"
    assert result[0].quantity == 3
    assert result[0].width == 600


def test_grouping_within_30mm_threshold():
    """Ширины 599/600/601 (различие ≤30мм) → 1 модуль quantity=3."""
    analyzer = make_analyzer()
    facades = [
        make_facade("lower", 599),
        make_facade("lower", 600),
        make_facade("lower", 601),
    ]
    result = analyzer._facades_to_modules(facades)
    assert len(result) == 1
    assert result[0].quantity == 3
    # avg = (599+600+601)//3 = 1800//3 = 600
    assert result[0].width == 600


def test_grouping_exact_30mm_difference():
    """Разница ровно 30мм (570 vs 600) → ОДНА группа (порог включительно)."""
    analyzer = make_analyzer()
    facades = [make_facade("lower", 570), make_facade("lower", 600)]
    result = analyzer._facades_to_modules(facades)
    assert len(result) == 1
    assert result[0].quantity == 2


def test_grouping_above_30mm_split():
    """Разница 31мм (569 vs 600) → ДВЕ группы (разрыв)."""
    analyzer = make_analyzer()
    facades = [make_facade("lower", 569), make_facade("lower", 600)]
    result = analyzer._facades_to_modules(facades)
    assert len(result) == 2
    assert all(m.quantity == 1 for m in result)


def test_avg_width_integer_division():
    """avg_width считается целочисленным делением // (округление вниз)."""
    analyzer = make_analyzer()
    # (600+601+602)//3 = 1803//3 = 601
    facades = [
        make_facade("lower", 600),
        make_facade("lower", 601),
        make_facade("lower", 602),
    ]
    result = analyzer._facades_to_modules(facades)
    assert result[0].width == 601


# ════════════════════════════════════════════════════════════════════
# ГЛУБИНЫ ПО ЗОНАМ
# ════════════════════════════════════════════════════════════════════

def test_lower_base_depth_default():
    """lower_base: depth = default_depth_lower (560)."""
    analyzer = make_analyzer()
    result = analyzer._facades_to_modules([make_facade("lower", 600)])
    assert result[0].depth == 560


def test_upper_base_depth_default():
    """upper_base: depth = default_depth_upper (320)."""
    analyzer = make_analyzer()
    result = analyzer._facades_to_modules([make_facade("upper", 600)])
    assert result[0].depth == 320


def test_zone_type_kitchen_depths():
    """Для zone_type='Кухня' те же стандартные глубины (560/320)."""
    analyzer = make_analyzer()
    facades = [make_facade("lower", 600), make_facade("upper", 600)]
    result = analyzer._facades_to_modules(facades, zone_type="Кухня")
    assert result[0].depth == 560  # lower
    assert result[1].depth == 320  # upper


# ════════════════════════════════════════════════════════════════════
# ПОРЯДОК ВЫВОДА (всегда lower → upper → penal)
# ════════════════════════════════════════════════════════════════════

def test_output_order_lower_upper_penal():
    """Порядок модулей на выходе всегда [lower, upper, penal], независимо от входа."""
    analyzer = make_analyzer()
    # Подадим в обратном порядке — penal, upper, lower
    facades = [
        make_facade("penal", 600, height_mm=2500),
        make_facade("upper", 600, height_mm=596),
        make_facade("lower", 600, height_mm=716),
    ]
    result = analyzer._facades_to_modules(facades)
    assert len(result) == 3
    assert result[0].type == "lower_base"
    assert result[1].type == "upper_base"
    assert result[2].type == "penal"


def test_unknown_zone_treated_as_lower():
    """Неизвестная зона → принудительно 'lower'."""
    analyzer = make_analyzer()
    facades = [ScaledFacade(zone="wardrobe", width_mm=600, height_mm=716,
                            bbox_x_pct=0, bbox_w_pct=20)]
    result = analyzer._facades_to_modules(facades)
    assert result[0].type == "lower_base"


# ════════════════════════════════════════════════════════════════════
# has_glass: 1-based индексация (после фикса B1)
# ════════════════════════════════════════════════════════════════════

def test_glass_indices_1_based():
    """glass_indices используют 1-based индексацию, как и drawer_indices.

    Промпт V2 просит has_glass_facades в формате 1-based: [3] = «третий фасад».
    После фикса B1 код делает (idx+1) in glass_indices — корректно 1-based.
    Поэтому glass_indices={3} помечает idx=2 (третий фасад) стеклом.
    """
    analyzer = make_analyzer()
    # 3 фасада, модель сказала has_glass_facades=[3] (1-based → «третий фасад»)
    facades = [
        make_facade("lower", 600),   # idx=0 (1-й)
        make_facade("lower", 600),   # idx=1 (2-й)
        make_facade("lower", 600),   # idx=2 (3-й) ← со стеклом
    ]
    result = analyzer._facades_to_modules(facades, glass_indices={3})

    # Группируются в 1 модуль (все по 600), any_glass = any((g[0]+1) in {3})
    # g[0]=2 → 2+1=3 in {3} → True → has_glass=True (стекло корректно!)
    assert result[0].has_glass is True


def test_glass_with_corner_facade():
    """Угловой фасад со стеклом: glass_indices 1-based корректно помечает угол.

    Угол задаётся ЯВНЫМ флагом is_corner=True (не шириной). Фасад idx=1
    (второй) → угол. glass_indices={2} → (1+1)=2 → стекло.
    """
    analyzer = make_analyzer()
    facades = [
        make_facade("lower", 600),   # idx=0 (1-й)
        ScaledFacade(zone="lower", width_mm=800, height_mm=820,
                     bbox_x_pct=0, bbox_w_pct=20, is_corner=True),  # idx=1 (2-й)
    ]
    result = analyzer._facades_to_modules(facades, glass_indices={2})
    corners = [m for m in result if m.type == "corner"]
    assert len(corners) == 1
    assert corners[0].has_glass is True


def test_glass_no_match():
    """glass_indices без совпадения → has_glass=False."""
    analyzer = make_analyzer()
    facades = [make_facade("lower", 600)]
    result = analyzer._facades_to_modules(facades, glass_indices={5})
    assert result[0].has_glass is False


# ════════════════════════════════════════════════════════════════════
# DRAWER_INDICES (корректно 1-based)
# ════════════════════════════════════════════════════════════════════

def test_drawer_indices_1_based_correct():
    """drawer_indices корректно 1-based: код делает (idx+1) in drawer_indices.

    Промпт даёт [2] = «второй фасад». idx второго = 1. (1+1)=2 in {2} → drawers.
    """
    analyzer = make_analyzer()
    facades = [
        make_facade("lower", 600),   # idx=0 (1-й)
        make_facade("lower", 600),   # idx=1 (2-й) ← с ящиком
        make_facade("lower", 600),   # idx=2 (3-й)
    ]
    result = analyzer._facades_to_modules(facades, drawer_indices={2})
    # Все 3 группируются в 1 модуль, any_drawer = any((g[0]+1) in {2})
    # g[0]=1 → 1+1=2 in {2} → True
    assert result[0].drawers == {"count": 1}


def test_drawer_indices_no_match():
    """drawer_indices без совпадения → drawers=None."""
    analyzer = make_analyzer()
    facades = [make_facade("lower", 600)]
    result = analyzer._facades_to_modules(facades, drawer_indices={5})
    assert result[0].drawers is None


# ════════════════════════════════════════════════════════════════════
# КОМБИНИРОВАННЫЙ СЦЕНАРИЙ (кухня)
# ════════════════════════════════════════════════════════════════════

def test_full_kitchen_scenario():
    """Полный сценарий: угол (по флагу) + 3 нижних + 1 верхний + 1 пенал."""
    analyzer = make_analyzer()
    facades = [
        ScaledFacade(zone="lower", width_mm=900, height_mm=820,
                     bbox_x_pct=0, bbox_w_pct=20, is_corner=True),  # угол
        make_facade("lower", 600, height_mm=820),     # lower
        make_facade("lower", 600, height_mm=820),     # lower (группируется)
        make_facade("upper", 600, height_mm=720),     # upper
        make_facade("penal", 600, height_mm=2500),    # penal
    ]
    result = analyzer._facades_to_modules(facades, zone_type="Кухня")

    # Ожидаем 4 модуля: 1 corner + 1 lower_base(qty=2) + 1 upper_base + 1 penal
    assert len(result) == 4
    types = [m.type for m in result]
    assert set(types) == {"corner", "lower_base", "upper_base", "penal"}

    by_type = {m.type: m for m in result}
    assert by_type["corner"].quantity == 1
    assert by_type["lower_base"].quantity == 2
    assert by_type["upper_base"].quantity == 1
    assert by_type["penal"].quantity == 1


def test_full_kitchen_wide_door_without_flag_not_corner():
    """Прямая кухня с широкой дверью 900 БЕЗ флага → НЕ угол (регресс P-loose).

    Реальный кейс: TV-тумба/шкаф с дверями ~900мм раньше превращались в
    «угловые модули» из-за эвристики 800–1100; теперь остаются lower_base.
    """
    analyzer = make_analyzer()
    facades = [
        make_facade("lower", 900, height_mm=820),   # широкая дверь, НЕ угол
        make_facade("lower", 600, height_mm=820),
        make_facade("lower", 600, height_mm=820),
    ]
    result = analyzer._facades_to_modules(facades, zone_type="Кухня")
    assert all(m.type != "corner" for m in result)
    # 600+600 группируются (qty=2), 900 отдельным модулем
    by_width = {m.width: m for m in result}
    assert by_width[600].quantity == 2
    assert by_width[900].quantity == 1


# ════════════════════════════════════════════════════════════════════
# MODULE-LEVEL facades_to_modules (FacadeData → RecognizedModule)
# ════════════════════════════════════════════════════════════════════

def test_module_level_empty_input():
    """Пустой список FacadeData → пустой результат."""
    assert facades_to_modules([]) == []


def test_module_level_penal_each_facade_separate():
    """В module-level версии каждый пенал → отдельный модуль quantity=1."""
    facades = [
        FacadeData(width_mm=600, height_mm=2500, zone="penal"),
        FacadeData(width_mm=600, height_mm=2500, zone="penal"),
    ]
    result = facades_to_modules(facades)
    assert len(result) == 2
    assert all(m.type == "penal" and m.quantity == 1 for m in result)


def test_module_level_corner_facade():
    """Угловой FacadeData → corner-модуль (width=depth)."""
    facades = [
        FacadeData(width_mm=900, height_mm=820, zone="lower", is_corner=True),
        FacadeData(width_mm=600, height_mm=820, zone="lower"),
    ]
    result = facades_to_modules(facades)
    corners = [m for m in result if m.type == "corner"]
    assert len(corners) == 1
    assert corners[0].width == 900
    assert corners[0].depth == 900  # квадратный


def test_module_level_unknown_zone_skipped():
    """Неизвестная зона (нет в VALID_SIZE) → модули этой зоны пропускаются."""
    facades = [
        FacadeData(width_mm=600, height_mm=820, zone="garage"),
        FacadeData(width_mm=600, height_mm=820, zone="lower"),
    ]
    result = facades_to_modules(facades)
    assert len(result) == 1
    assert result[0].type == "lower_base"


def test_module_level_penalty_size_correction():
    """Ширина вне [250,1200] → стандартная std_w для зоны."""
    # penal с шириной 200 (< 250) → std_w=600
    facades = [FacadeData(width_mm=200, height_mm=2500, zone="penal")]
    result = facades_to_modules(facades)
    assert result[0].width == 600  # исправлено на стандарт


def test_module_level_each_facade_separate_quantity():
    """В module-level версии обычные фасады НЕ группируются — каждый quantity=1.

    В отличие от _facades_to_modules (группирует по ширине с quantity=N),
    эта функция создаёт отдельный модуль на каждый фасад.
    """
    facades = [
        FacadeData(width_mm=600, height_mm=716, zone="lower"),
        FacadeData(width_mm=600, height_mm=716, zone="lower"),
        FacadeData(width_mm=600, height_mm=716, zone="lower"),
    ]
    result = facades_to_modules(facades)
    assert len(result) == 3  # 3 отдельных модуля, не 1 с quantity=3
    assert all(m.quantity == 1 for m in result)


# ════════════════════════════════════════════════════════════════════
# ТИП ВОЗВРАЩАЕМЫХ ЗНАЧЕНИЙ
# ════════════════════════════════════════════════════════════════════

def test_returns_recognized_module_instances():
    """Возвращаемые элементы — экземпляры RecognizedModule."""
    analyzer = make_analyzer()
    result = analyzer._facades_to_modules([make_facade("lower", 600)])
    assert isinstance(result[0], RecognizedModule)
    assert isinstance(result[0].quantity, int)
    assert isinstance(result[0].has_glass, bool)
