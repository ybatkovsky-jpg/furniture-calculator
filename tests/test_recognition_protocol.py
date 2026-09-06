"""
Тесты протокола стабильности распознавания (чистые функции, без сети и моделей).

Покрывают:
- scale_ok_meta: точная сумма, учёт quantity, порог max(200, 5% total),
  fallback на все модули при отсутствии нижних;
- choose_attempt: голосование 2/3, приоритет scale_ok, confidence-тайбрейк,
  тайбрейк |sum−total|, needs_operator;
- build_standard_modules: распределение total/door_count, кламп 300..1000,
  fallback 600, высоты/глубины по зонам;
- door_count_of / door_counts_by_zone.
"""

from app.services.recognition_protocol import (
    build_standard_modules,
    choose_attempt,
    door_count_of,
    door_counts_by_zone,
    plausible_total,
    scale_ok_meta,
)


# ── Фабрики ──

def _mod(mtype="lower_base", width=600, depth=560, height=716, quantity=1, **kw):
    m = {
        "type": mtype, "width": width, "depth": depth, "height": height,
        "quantity": quantity, "has_glass": False,
        "facades": {"count": 1, "type": "doors"},
        "drawers": None, "shelves": 0, "is_corner": False, "bbox": None,
    }
    m.update(kw)
    return m


def _attempt(modules, zone_type="Кухня", total=1800, scale_ok=False,
             confidence="medium", door_count=None):
    return {
        "modules": modules,
        "zone_type": zone_type,
        "materials": [],
        "confidence": confidence,
        "total_width_mm": total,
        "scale_ok": scale_ok,
        "door_count": door_count if door_count is not None else door_count_of(modules),
    }


# ════════════════════════════════════════════════════════════════
# scale_ok_meta
# ════════════════════════════════════════════════════════════════

def test_scale_ok_exact_sum():
    """Точная сумма: 3×600 = 1800 = total → True."""
    modules = [_mod(width=600), _mod(width=600), _mod(width=600)]
    assert scale_ok_meta(modules, 1800) is True


def test_scale_ok_quantity_multiplied():
    """quantity=3: ширина суммируется 3 раза (600×3 = 1800)."""
    modules = [_mod(width=600, quantity=3)]
    assert scale_ok_meta(modules, 1800) is True


def test_scale_ok_within_5_percent():
    """total=3000: допуск max(200, 150)=200; разница 150 проходит."""
    modules = [_mod(width=1050, quantity=3)]  # 3150 − 3000 = 150
    assert scale_ok_meta(modules, 3000) is True


def test_scale_ok_outside_5_percent():
    """total=3000: разница 250 > 200 → False."""
    modules = [_mod(width=3250, quantity=1)]
    assert scale_ok_meta(modules, 3000) is False


def test_scale_ok_200_floor_for_small_totals():
    """total=1000: 5% = 50 → допуск 200 (минимум). 200 проходит, 250 нет."""
    modules_ok = [_mod(width=800)]       # разница 200
    modules_bad = [_mod(width=750)]      # разница 250
    assert scale_ok_meta(modules_ok, 1000) is True
    assert scale_ok_meta(modules_bad, 1000) is False


def test_scale_ok_uses_only_lower_and_corner():
    """В сумму входят только lower_base/corner; upper/penal игнорируются."""
    modules = [
        _mod(width=600),                          # lower
        _mod(mtype="corner", width=900, depth=900),  # corner
        _mod(mtype="upper_base", width=600, depth=320, height=596),
        _mod(mtype="penal", width=600, height=2500),
    ]
    assert scale_ok_meta(modules, 1500) is True   # 600 + 900


def test_scale_ok_falls_back_to_all_modules_without_lower():
    """Нижних нет → проверка по ВСЕМ модулям (2 upper × 600 = 1200)."""
    modules = [
        _mod(mtype="upper_base", width=600, depth=320, height=596),
        _mod(mtype="upper_base", width=600, depth=320, height=596),
    ]
    assert scale_ok_meta(modules, 1200) is True
    assert scale_ok_meta(modules, 3000) is False


def test_scale_ok_empty_modules_false():
    assert scale_ok_meta([], 1800) is False


def test_scale_ok_zero_or_missing_total_false():
    modules = [_mod(width=600)]
    assert scale_ok_meta(modules, 0) is False
    assert scale_ok_meta(modules, None) is False


def test_scale_ok_implausible_total_false():
    """Бредовый габарит (9360 > 8000) не проходит масштаб даже при совпадении сумм."""
    assert scale_ok_meta([_mod(width=9360, quantity=1)], 9360) is False


def test_plausible_total_bounds():
    assert plausible_total(400) is True
    assert plausible_total(8000) is True
    assert plausible_total(399) is False
    assert plausible_total(8001) is False
    assert plausible_total(0) is False
    assert plausible_total(None) is False


# ════════════════════════════════════════════════════════════════
# choose_attempt
# ════════════════════════════════════════════════════════════════

def test_choose_consensus_two_of_three_agree():
    """2 из 3 scale_ok-попыток согласны (зона, total±100, двери) → голосование."""
    a1 = _attempt([_mod(width=600, quantity=3)], zone_type="Кухня", total=1800, scale_ok=True)
    a2 = _attempt([_mod(width=600, quantity=3)], zone_type="Кухня", total=1850, scale_ok=True)
    a3 = _attempt([_mod(width=600, quantity=2)], zone_type="Ванная", total=1200, scale_ok=True)
    result = choose_attempt([a1, a2, a3])
    assert result["consensus"] is True
    assert result["chosen"]["zone_type"] == "Кухня"
    assert result["needs_operator"] is False


def test_choose_agreement_without_scale_is_not_accepted():
    """РЕГРЕСС (случай 9360мм): 2 попытки согласны, но масштаб НЕ сошёлся →
    голосование бредовых чисел НЕ считается истиной, needs_operator=True."""
    a1 = _attempt([_mod(width=900, quantity=8)], zone_type="Кухня", total=9360, scale_ok=False)
    a2 = _attempt([_mod(width=900, quantity=8)], zone_type="Кухня", total=9360, scale_ok=False)
    result = choose_attempt([a1, a2])
    assert result["consensus"] is False
    assert result["needs_operator"] is True
    assert result["chosen"]["total_width_mm"] == 9360  # только для fallback


def test_choose_no_consensus_when_totals_diverge():
    """totals расходятся >100мм → голосования нет."""
    a1 = _attempt([_mod(width=600, quantity=3)], total=1800)
    a2 = _attempt([_mod(width=900, quantity=3)], total=2700)
    a3 = _attempt([_mod(width=600, quantity=3)], total=2050, confidence="low")
    result = choose_attempt([a1, a2, a3])
    assert result["consensus"] is False
    assert result["needs_operator"] is True


def test_choose_prioritizes_scale_ok_over_confidence():
    """scale_ok=True побеждает даже против high-уверенности без scale_ok."""
    a1 = _attempt([_mod(width=600, quantity=3)], total=1800,
                  scale_ok=False, confidence="high")
    a2 = _attempt([_mod(width=600, quantity=3)], total=1800,
                  scale_ok=True, confidence="low")
    result = choose_attempt([a1, a2])
    assert result["chosen"]["scale_ok"] is True
    assert result["chosen"]["confidence"] == "low"
    assert result["consensus"] is False
    assert result["needs_operator"] is False  # есть scale_ok-попытка


def test_choose_confidence_tiebreak():
    """Без scale_ok и голосования: побеждает high > medium."""
    a1 = _attempt([_mod(width=600, quantity=3)], total=1800, confidence="medium")
    a2 = _attempt([_mod(width=600, quantity=3)], total=2900, confidence="high")
    result = choose_attempt([a1, a2])
    assert result["chosen"]["confidence"] == "high"
    assert result["chosen"]["total_width_mm"] == 2900
    assert result["consensus"] is False
    assert result["needs_operator"] is True


def test_choose_sum_delta_tiebreak():
    """Одинаковый confidence → тайбрейк по минимальной |sum−total|."""
    a1 = _attempt([_mod(width=600, quantity=3)], total=2000)   # |1800−2000| = 200
    a2 = _attempt([_mod(width=600, quantity=3)], total=2600)   # |1800−2600| = 800
    result = choose_attempt([a1, a2])
    assert result["chosen"]["total_width_mm"] == 2000
    assert result["needs_operator"] is True


def test_choose_filters_empty_modules():
    """Пустые modules отбрасываются: победить может только непустая попытка."""
    a1 = _attempt([], zone_type="Кухня", total=1800)
    a2 = _attempt([_mod(width=600, quantity=3)], zone_type="Кухня", total=1800,
                  scale_ok=True)
    result = choose_attempt([a1, a2])
    assert result["chosen"] is a2
    assert result["needs_operator"] is False


def test_choose_all_empty_returns_no_chosen():
    result = choose_attempt([
        _attempt([], total=1800),
        _attempt([], zone_type="Ванная", total=1200),
    ])
    assert result["chosen"] == {}
    assert result["consensus"] is False
    assert result["needs_operator"] is True


def test_choose_empty_list():
    result = choose_attempt([])
    assert result["chosen"] == {}
    assert result["needs_operator"] is True


def test_choose_single_scale_ok_attempt():
    """Одна scale_ok-попытка: берём её, consensus=False, operator не нужен."""
    a1 = _attempt([_mod(width=600, quantity=3)], total=1800, scale_ok=True)
    result = choose_attempt([a1])
    assert result["chosen"] is a1
    assert result["consensus"] is False
    assert result["needs_operator"] is False


def test_choose_consensus_uses_door_count_from_modules():
    """Число дверей для голосования берётся из modules, если door_count нет."""
    a1 = _attempt([_mod(width=600, quantity=3)], total=1800, door_count=None, scale_ok=True)
    a2 = _attempt([_mod(width=600, quantity=3)], total=1850, door_count=None, scale_ok=True)
    a3 = _attempt([_mod(width=600, quantity=2)], total=1200, door_count=None, scale_ok=True)
    result = choose_attempt([a1, a2, a3])
    assert result["consensus"] is True
    assert result["chosen"]["door_count"] == 3


# ════════════════════════════════════════════════════════════════
# build_standard_modules
# ════════════════════════════════════════════════════════════════

def test_standard_distributes_total_over_doors():
    """total=2400, 4 двери → 600 на дверь, один модуль qty=4."""
    mods = build_standard_modules("Кухня", 2400, 4)
    assert len(mods) == 1
    assert mods[0]["type"] == "lower_base"
    assert mods[0]["width"] == 600
    assert mods[0]["quantity"] == 4
    assert mods[0]["height"] == 716
    assert mods[0]["depth"] == 560  # get_rules("Кухня").default_depth_lower


def test_standard_rounding_of_door_width():
    """total=2500, 3 двери → round(833.33) = 833."""
    mods = build_standard_modules("Кухня", 2500, 3)
    assert mods[0]["width"] == 833


def test_standard_clamp_to_1000():
    """total=4500, 4 двери → 1125 → кламп 1000."""
    mods = build_standard_modules("Кухня", 4500, 4)
    assert mods[0]["width"] == 1000


def test_standard_clamp_to_300():
    """total=900, 4 двери → 225 → кламп 300."""
    mods = build_standard_modules("Кухня", 900, 4)
    assert mods[0]["width"] == 300


def test_standard_fallback_600_when_no_total():
    """total<=0 → стандарт 600 на дверь."""
    mods_zero = build_standard_modules("Кухня", 0, 3)
    mods_none = build_standard_modules("Кухня", None, 3)
    assert mods_zero[0]["width"] == 600
    assert mods_none[0]["width"] == 600
    assert mods_zero[0]["quantity"] == 3


def test_standard_per_zone_counts():
    """dict зон: 2 lower + 3 upper, total=3000 → 600 на дверь по обеим зонам."""
    mods = build_standard_modules("Кухня", 3000, {"lower": 2, "upper": 3})
    assert len(mods) == 2
    lower = next(m for m in mods if m["type"] == "lower_base")
    upper = next(m for m in mods if m["type"] == "upper_base")
    assert lower["width"] == 600 and lower["quantity"] == 2
    assert lower["height"] == 716 and lower["depth"] == 560
    assert upper["width"] == 600 and upper["quantity"] == 3
    assert upper["height"] == 596 and upper["depth"] == 320


def test_standard_penal_zone():
    """Пенал: высота 2500, глубина как у нижних баз."""
    mods = build_standard_modules("Кухня", 600, {"penal": 1})
    assert mods[0]["type"] == "penal"
    assert mods[0]["height"] == 2500
    assert mods[0]["depth"] == 560


def test_standard_zero_doors_returns_empty():
    assert build_standard_modules("Кухня", 2400, 0) == []
    assert build_standard_modules("Кухня", 2400, {}) == []


def test_standard_unknown_zone_uses_default_rules():
    """Неизвестная зона → FurnitureRules() по умолчанию (560/320)."""
    mods = build_standard_modules("Какая-то зона", 1200, 2)
    assert mods[0]["depth"] == 560


# ════════════════════════════════════════════════════════════════
# door_count_of / door_counts_by_zone
# ════════════════════════════════════════════════════════════════

def test_door_count_quantity_times_facades():
    assert door_count_of([_mod(quantity=3)]) == 3
    assert door_count_of([_mod(quantity=2, facades={"count": 2, "type": "doors"})]) == 4


def test_door_count_empty():
    assert door_count_of([]) == 0


def test_door_counts_by_zone_mapping():
    mods = [
        _mod(quantity=3),                                       # lower ×3
        _mod(mtype="upper_base", depth=320, height=596, quantity=2),
        _mod(mtype="penal", height=2500),                       # penal ×1
        _mod(mtype="corner", width=900, depth=900),             # lower ×1
    ]
    counts = door_counts_by_zone(mods)
    assert counts == {"lower": 4, "upper": 2, "penal": 1}
