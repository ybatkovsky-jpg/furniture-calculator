"""
Протокол стабильности распознавания (чистые функции, без сети и моделей).

ТЗ заказчика: «берём ширины и высоты и сопоставляем; если масштаб сходится
с картинкой — принимаем за истину; если нет — читаем картинку ещё раз;
если размеров нет — считаем по средним общепринятым размерам, масштабируя
на всю мебель».

Функции:
- scale_ok_meta(modules, total_width_mm)   — сходится ли сумма ширин с габаритом
- choose_attempt(attempts)                 — приём решения по N попыткам
- build_standard_modules(...)              — fallback «нет размеров» (средние размеры)
- door_count_of / door_counts_by_zone      — счёт дверей по модулям

Модули принимаются и как RecognizedModule (атрибуты), и как dict'ы (ключи) —
это делает функции удобными для тестов без импорта image_analyzer.
"""

from typing import Any, Dict, List, Optional

# Ранг уверенности для тайбрейков (high > medium > low)
CONF_RANK = {"high": 3, "medium": 2, "low": 1}

# Правдоподобный габарит мебели (мм): вне диапазона число не считаем размером
# (например, модель дважды «прочитала» 9360 на кухне 4860 — это не габарит).
PLAUSIBLE_TOTAL_MIN, PLAUSIBLE_TOTAL_MAX = 400, 8000


def plausible_total(value) -> bool:
    """Число похоже на габарит изделия? (400..8000 мм)."""
    v = value or 0
    return PLAUSIBLE_TOTAL_MIN <= v <= PLAUSIBLE_TOTAL_MAX


# ════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНОЕ
# ════════════════════════════════════════════════════════════════

def _field(obj, name: str, default=None):
    """Поле модуля: атрибут RecognizedModule или ключ dict'а."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def door_count_of(modules) -> int:
    """Число дверей: Σ quantity × facades.count (без facades → quantity)."""
    total = 0
    for m in modules or []:
        q = int(_field(m, "quantity", 1) or 1)
        facades = _field(m, "facades", None)
        cnt = 1
        if isinstance(facades, dict):
            cnt = int(facades.get("count", 1) or 1)
        total += max(q, 1) * max(cnt, 1)
    return total


def door_counts_by_zone(modules) -> Dict[str, int]:
    """Число дверей по зонам {'lower', 'upper', 'penal'} (по типам модулей)."""
    counts = {"lower": 0, "upper": 0, "penal": 0}
    for m in modules or []:
        q = int(_field(m, "quantity", 1) or 1)
        facades = _field(m, "facades", None)
        cnt = int(facades.get("count", 1)) if isinstance(facades, dict) else 1
        doors = max(q, 1) * max(cnt or 1, 1)
        mtype = _field(m, "type", "") or ""
        if mtype in ("penal", "column", "tall_cabinet", "wardrobe"):
            counts["penal"] += doors
        elif mtype == "upper_base":
            counts["upper"] += doors
        else:  # lower_base, corner и всё прочее — нижний ряд
            counts["lower"] += doors
    return {k: v for k, v in counts.items() if v > 0}


def _width_sum(modules) -> int:
    """Сумма ширин модулей с учётом quantity."""
    total = 0
    for m in modules or []:
        w = int(_field(m, "width", 0) or 0)
        q = max(int(_field(m, "quantity", 1) or 1), 1)
        total += w * q
    return total


# ════════════════════════════════════════════════════════════════
# МАСШТАБНЫЙ ЧЕК
# ════════════════════════════════════════════════════════════════

def scale_ok_meta(modules, total_width_mm) -> bool:
    """
    Масштаб сходится с картинкой?

    Сумма ширин нижних модулей (lower_base/corner) сравнивается с габаритом
    total_width_mm с допуском max(200, 5% от total). Если нижних модулей нет —
    по ВСЕМ модулям. Модули с quantity>1 учитываются как width × quantity.

    Пустые модули или total<=0 → False (масштаб проверить нечем).
    """
    if not modules:
        return False
    total = total_width_mm or 0
    if total <= 0 or not plausible_total(total):
        return False
    lower = [m for m in modules if _field(m, "type", "") in ("lower_base", "corner")]
    pool = lower or list(modules)
    s = _width_sum(pool)
    tolerance = max(200.0, total * 0.05)
    return abs(s - total) <= tolerance


# ════════════════════════════════════════════════════════════════
# ПРИЁМ РЕШЕНИЯ ПО ПОПЫТКАМ
# ════════════════════════════════════════════════════════════════

def _sum_delta(attempt: dict) -> float:
    """|сумма ширин − total| — метрика для тайбрейка."""
    total = attempt.get("total_width_mm") or 0
    return abs(_width_sum(attempt.get("modules") or []) - total)


def _agree(a: dict, b: dict) -> bool:
    """Две попытки согласны: зона совпадает, total в пределах ±100мм,
    число дверей равно."""
    if (a.get("zone_type") or "") != (b.get("zone_type") or ""):
        return False
    if abs((a.get("total_width_mm") or 0) - (b.get("total_width_mm") or 0)) > 100:
        return False
    da = a.get("door_count")
    db = b.get("door_count")
    if da is None:
        da = door_count_of(a.get("modules") or [])
    if db is None:
        db = door_count_of(b.get("modules") or [])
    return da == db


def _best_of(pool: List[dict]) -> dict:
    """Лучшая попытка пула: максимум confidence (high>medium>low),
    тайбрейк — минимальная |sum − total|."""

    def rank(a: dict) -> int:
        return CONF_RANK.get(a.get("confidence") or "", 0)

    return max(pool, key=lambda a: (rank(a), -_sum_delta(a)))


def choose_attempt(attempts: List[dict]) -> dict:
    """
    Приём решения по попыткам распознавания.

    attempt = {"modules", "zone_type", "materials", "confidence",
               "total_width_mm", "scale_ok", "door_count"?}

    Правила:
    1. Попытки с пустыми modules отбрасываются.
    2. РЕШЕНИЕ принимается ТОЛЬКО по попыткам, где масштаб сошёлся (scale_ok).
       Голосование и confidence-тайбрейки работают внутри этого пула:
       если ≥2 scale_ok-попытки согласны по (zone_type, total±100, число дверей)
       → берём группу (consensus); иначе максимум confidence (high>medium>low),
       тайбрейк — минимальная |sum − total|.
    3. Если НИ ОДНА попытка не прошла масштаб — результат НЕ принимается
       (needs_operator=True): «стабильность» бредовых чисел не считается истиной.

    Returns {"chosen", "consensus", "needs_operator"}.
    needs_operator=True, если нет ни одной scale_ok-попытки.
    """
    filled = [a for a in attempts if a and a.get("modules")]
    if not filled:
        return {"chosen": {}, "consensus": False, "needs_operator": True}

    scale_ok_pool = [a for a in filled if a.get("scale_ok")]

    chosen: Optional[dict] = None
    consensus = False

    if scale_ok_pool:
        pool = scale_ok_pool
        # Голосование: самая большая группа согласных scale_ok-попыток (размер ≥2)
        if len(pool) >= 2:
            best_group: List[dict] = []
            for a in pool:
                group = [b for b in pool if _agree(a, b)]
                if len(group) >= 2 and len(group) > len(best_group):
                    best_group = group
            if best_group:
                consensus = True
                chosen = _best_of(best_group)
        if chosen is None:
            chosen = _best_of(pool)
    else:
        # Масштаб не сошёлся ни разу: chosen нужен только для fallback
        # «стандартные размеры», результат НЕ принимаем.
        chosen = _best_of(filled)

    needs_operator = not scale_ok_pool
    return {"chosen": chosen, "consensus": consensus, "needs_operator": needs_operator}


# ════════════════════════════════════════════════════════════════
# FALLBACK «НЕТ РАЗМЕРОВ» — СРЕДНИЕ ОБЩЕПРИНЯТЫЕ РАЗМЕРЫ
# ════════════════════════════════════════════════════════════════

def build_standard_modules(zone_type, total_width_mm, door_count) -> List[dict]:
    """
    Fallback «нет размеров»: честный средний размер, масштабированный
    на всю мебель.

    - total > 0: ширина двери = round(total/door_count), кламп 300..1000 мм;
    - total <= 0: стандарт ширины 600 мм на дверь.
    - Высоты по зонам из scale_calc.DEFAULT_HEIGHTS (lower=716/upper=596/penal=2500).
    - Глубина по furniture_defaults.get_rules(zone_type).
    - door_count: int (все двери → нижний ряд) ИЛИ dict {"lower"/"upper"/"penal": n}
      (число дверей по зонам — например, door_counts_by_zone(chosen.modules)).

    Возвращает список dict'ов RecognizedModule-полей: модули группируются
    по зоне, quantity = число дверей зоны.
    """
    from app.services.scale_calc import DEFAULT_HEIGHTS
    from app.services.furniture_defaults import get_rules

    rules = get_rules(zone_type)

    if isinstance(door_count, dict):
        counts = {k: int(v) for k, v in door_count.items() if int(v or 0) > 0}
    else:
        n = int(door_count or 0)
        counts = {"lower": n} if n > 0 else {}

    total_doors = sum(counts.values())
    if total_doors <= 0:
        return []

    if total_width_mm and total_width_mm > 0:
        width = round(total_width_mm / total_doors)
    else:
        width = 600
    width = max(300, min(1000, width))

    modules = []
    zone_specs = (
        ("lower", "lower_base", rules.default_depth_lower),
        ("upper", "upper_base", rules.default_depth_upper),
        ("penal", "penal", rules.default_depth_lower),
    )
    for zone, mtype, depth in zone_specs:
        cnt = counts.get(zone, 0)
        if cnt <= 0:
            continue
        modules.append({
            "type": mtype,
            "width": width,
            "depth": depth,
            "height": DEFAULT_HEIGHTS[zone],
            "quantity": cnt,
            "has_glass": False,
            "facades": {"count": 1, "type": "doors"},
            "drawers": None,
            "shelves": 0,
            "is_corner": False,
            "bbox": None,
        })
    return modules
