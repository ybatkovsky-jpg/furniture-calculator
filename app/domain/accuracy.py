"""Допуск точности КП: ±10 % (решение 24.11.2026).

Сравнение идёт по итоговой цене клиента (наличные/безнал) и себестоимости.
Нулевая ожидаемая цена — ошибка данных, а не «идеальное совпадение».
"""

from __future__ import annotations

DEFAULT_TOLERANCE = 0.10


def relative_error(actual: float, expected: float) -> float:
    """Относительная ошибка actual против expected (0.10 = 10 %)."""
    if expected == 0:
        raise ValueError("Ожидаемая цена равна нулю — сравнивать нечего")
    return abs(float(actual) - float(expected)) / abs(float(expected))


def within_kp_tolerance(
    actual: float,
    expected: float,
    tolerance: float = DEFAULT_TOLERANCE,
) -> bool:
    """Укладывается ли КП в допуск (по умолчанию ±10 %)."""
    return relative_error(actual, expected) <= tolerance


def compare_kp(
    actual: dict[str, float],
    expected: dict[str, float],
    tolerance: float = DEFAULT_TOLERANCE,
) -> dict:
    """Сравнить набор сумм; вернуть отчёт с флагом и максимальной ошибкой."""
    rows = []
    for key, expected_value in expected.items():
        if key not in actual:
            rows.append({"key": key, "error": None, "ok": False, "missing": True})
            continue
        error = relative_error(actual[key], expected_value)
        rows.append(
            {
                "key": key,
                "actual": actual[key],
                "expected": expected_value,
                "error": round(error, 4),
                "ok": error <= tolerance,
                "missing": False,
            }
        )
    worst = max((row["error"] for row in rows if row["error"] is not None), default=0.0)
    return {
        "tolerance": tolerance,
        "ok": all(row["ok"] for row in rows),
        "max_error": worst,
        "rows": rows,
    }
