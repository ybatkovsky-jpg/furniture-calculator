"""
Тесты для расчёта крепежа и установочной фурнитуры (fastener_calc).

Покрывают calculate_fasteners (чистая функция, без I/O) и обёртку
calculate_fasteners_from_modules. Фиксируют текущее поведение, включая
граничные случаи (нули, отрицательные значения, запас 10%, минификсы),
как регрессионный базлайн.
"""

from types import SimpleNamespace

from app.services.fastener_calc import (
    FastenerQuantities,
    calculate_fasteners,
    calculate_fasteners_from_modules,
)


def _module(type_, width_mm=0, quantity=1, shelves=0, facades=None):
    """Стуб RecognizedModule: только атрибуты, которые читает обёртка."""
    return SimpleNamespace(
        type=type_,
        width=width_mm,
        quantity=quantity,
        shelves=shelves,
        facades=facades,
    )


# ════════════════════════════════════════════════════════════════════
# ГРАНИЧНЫЕ СЛУЧАИ: пустой ввод / нули
# ════════════════════════════════════════════════════════════════════

def test_all_defaults_returns_zero_quantities():
    """Все аргументы по умолчанию → все количества 0, total_cost = 0.0."""
    q = calculate_fasteners()
    assert q.confirmat_7x50 == 0
    assert q.self_tapping_35x16 == 0
    assert q.adjustable_feet == 0
    assert q.wall_mounts == 0
    assert q.plinth_strips == 0
    assert q.plinth_corners == 0
    assert q.minifix_housings == 0
    assert q.minifix_bolts == 0
    assert q.dowels_8x30 == 0
    assert q.total_cost == 0.0


def test_self_tapping_zero_for_zero_and_one_module():
    """Саморезы на стыки: 0 модулей → max(0, -1)=0; 1 модуль → 0 стыков."""
    assert calculate_fasteners(total_modules=0).self_tapping_35x16 == 0
    assert calculate_fasteners(total_modules=1).self_tapping_35x16 == 0


def test_negative_width_no_plinth():
    """Цоколь: ширина <= 0 → защита `if width > 0` не срабатывает, планок нет."""
    q = calculate_fasteners(lower_modules_width_m=-5.0)
    assert q.plinth_strips == 0
    assert q.plinth_corners == 0


def test_negative_inputs_not_clamped():
    """Отрицательные входы НЕ обрезаются до 0 (фиксация поведения).

    shelves_count=-1 → base = -4 → ceil(-4*1.1) = -4: количество отрицательное.
    lower_module_count=-2 → опоры -8. Защита от «мусора» на входе отсутствует.
    """
    q = calculate_fasteners(shelves_count=-1)
    assert q.confirmat_7x50 == -4

    q2 = calculate_fasteners(lower_module_count=-2)
    assert q2.adjustable_feet == -8


# ════════════════════════════════════════════════════════════════════
# АРИФМЕТИКА КОНФИРМАТОВ (ядро: формула + запас 10%)
# ════════════════════════════════════════════════════════════════════

def test_confirmat_formula_with_margin():
    """Формула: (полки×4)+(крышки×6)+(днища×6)+(перегородки×4), +10% запас.

    total=2, shelves=2, partitions=1 → base = 8+12+12+4 = 36
    ceil(36*1.1) = ceil(39.6) = 40. Саморезы: (2-1)*4 = 4.
    """
    q = calculate_fasteners(total_modules=2, shelves_count=2, partitions_count=1)
    assert q.confirmat_7x50 == 40
    assert q.self_tapping_35x16 == 4


def test_confirmat_margin_rounds_up():
    """Запас 10% округляется вверх: base=16 → ceil(17.6)=18, а не 17."""
    # total=1 (крышка+дно), shelves=1 → base = 4+6+6 = 16
    q = calculate_fasteners(total_modules=1, shelves_count=1)
    assert q.confirmat_7x50 == 18


def test_self_tapping_by_joint_count():
    """Саморезы: по 4 на каждый стык (total-1), если total > 1."""
    assert calculate_fasteners(total_modules=3).self_tapping_35x16 == 8
    assert calculate_fasteners(total_modules=5).self_tapping_35x16 == 16


# ════════════════════════════════════════════════════════════════════
# ОПОРЫ, ПОДВЕСЫ, ЦОКОЛЬ
# ════════════════════════════════════════════════════════════════════

def test_feet_and_mounts():
    """Опоры: 4 на нижнюю базу; подвесы: 2 на верхнюю базу."""
    q = calculate_fasteners(lower_module_count=2, upper_module_count=3)
    assert q.adjustable_feet == 8
    assert q.wall_mounts == 6


def test_plinth_strips_formula():
    """Цоколь: ceil(ширина*1.1/4) планок по 4м + минимум 2 уголка.

    width=4.0 → ceil(4.4/4)=ceil(1.1)=2; width=3.6 → ceil(3.96/4)=ceil(0.99)=1.
    """
    q = calculate_fasteners(lower_modules_width_m=4.0)
    assert q.plinth_strips == 2
    assert q.plinth_corners == 2

    q2 = calculate_fasteners(lower_modules_width_m=3.6)
    assert q2.plinth_strips == 1
    assert q2.plinth_corners == 2


# ════════════════════════════════════════════════════════════════════
# МИНИФИКСЫ (опциональная ветка)
# ════════════════════════════════════════════════════════════════════

def test_minifix_replaces_confirmats():
    """use_minifix=True: joints = shelves+крышки+днища+перегородки, ×2 на каждый.

    shelves=2, total=1 (крышка+дно), partitions=1 → joints=5
    → корпуса/штоки/шканты по 10; конфирматы обнуляются.
    """
    q = calculate_fasteners(
        total_modules=1, shelves_count=2, partitions_count=1, use_minifix=True,
    )
    assert q.minifix_housings == 10
    assert q.minifix_bolts == 10
    assert q.dowels_8x30 == 10
    assert q.confirmat_7x50 == 0


def test_minifix_zero_inputs():
    """use_minifix=True при нулевом вводе → все минификсы 0, конфирматы 0."""
    q = calculate_fasteners(use_minifix=True)
    assert q.minifix_housings == 0
    assert q.minifix_bolts == 0
    assert q.dowels_8x30 == 0
    assert q.confirmat_7x50 == 0


# ════════════════════════════════════════════════════════════════════
# ОБЁРТКА calculate_fasteners_from_modules
# ════════════════════════════════════════════════════════════════════

def test_from_modules_empty_list():
    """Пустой список модулей → все количества 0."""
    q = calculate_fasteners_from_modules([])
    assert q.confirmat_7x50 == 0
    assert q.self_tapping_35x16 == 0
    assert q.adjustable_feet == 0
    assert q.wall_mounts == 0
    assert q.plinth_strips == 0


def test_from_modules_aggregates_bases():
    """Две нижние базы (qty 2 и 1, по 800мм) — агрегация всех полей.

    total=3 → саморезы (3-1)*4=8; опоры 3*4=12.
    Ширина 0.8*3=2.4м → ceil(2.64/4)=1 планка, 2 уголка.
    У каждой базы shelves=0 → считается 1 «полка» (max(shelves, 1)) → shelves=3.
    Конфирматы: 3*4 + 3*6 + 3*6 = 48 → ceil(52.8)=53.
    """
    modules = [
        _module("lower_base", width_mm=800, quantity=2),
        _module("lower_base", width_mm=800, quantity=1),
    ]
    q = calculate_fasteners_from_modules(modules)
    assert q.self_tapping_35x16 == 8
    assert q.adjustable_feet == 12
    assert q.plinth_strips == 1
    assert q.plinth_corners == 2
    assert q.confirmat_7x50 == 53


def test_from_modules_quantity_falls_back_to_one():
    """quantity=0 (или меньше) → max(quantity, 1): модуль всё равно считается."""
    modules = [_module("lower_base", width_mm=600, quantity=0)]
    q = calculate_fasteners_from_modules(modules)
    # total=1: саморезы 0; опоры 4; shelves=max(0,1)*1=1
    assert q.self_tapping_35x16 == 0
    assert q.adjustable_feet == 4
    # ширина 0.6м → ceil(0.66/4)=1 планка, 2 уголка
    assert q.plinth_strips == 1
    assert q.plinth_corners == 2
    # конфирматы: 1*4 + 1*6 + 1*6 = 16 → ceil(17.6)=18
    assert q.confirmat_7x50 == 18


def test_from_modules_partitions_from_facades_count():
    """Перегородки: facades.count>1 → (count-1)*qty; count=1/пустой/None → 0."""
    modules = [
        _module("penal", facades={"count": 3}),   # +2 перегородки
        _module("penal", facades={"count": 1}),   # не > 1 → 0
        _module("penal", facades={}),             # пустой dict (falsy) → 0
        _module("penal", facades=None),           # None → 0
    ]
    q = calculate_fasteners_from_modules(modules)
    # total=4 → саморезы (4-1)*4=12; перегородки=2
    # конфирматы: 0*4 + 4*6 + 4*6 + 2*4 = 56 → ceil(61.6)=62
    assert q.self_tapping_35x16 == 12
    assert q.confirmat_7x50 == 62


# ════════════════════════════════════════════════════════════════════
# ТИП ВОЗВРАЩАЕМОГО ЗНАЧЕНИЯ
# ════════════════════════════════════════════════════════════════════

def test_returns_dataclass_instance():
    """Результат — экземпляр FastenerQuantities, количества — int."""
    q = calculate_fasteners(total_modules=1)
    assert isinstance(q, FastenerQuantities)
    assert isinstance(q.confirmat_7x50, int)
    assert isinstance(q.self_tapping_35x16, int)
