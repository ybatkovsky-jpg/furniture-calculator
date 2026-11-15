"""Денежные операции с Decimal (P1-24).

`float` остаётся только на границе с JSON/Excel/БД; все расчёты денег идут
через Decimal с явным правилом округления:

- деньги — `ROUND_HALF_UP` до копеек;
- количество листов/метров — `ROUND_CEILING` (нельзя купить 0,2 листа).

В SQLite деньги пока хранятся как Float (историческая схема). Переход на
целые копейки потребует миграции всех денежных колонок — вынесено в этап 4.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


def to_decimal(value: Any) -> Decimal:
    """Безопасно привести число к Decimal через str (без float-хвостов)."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal(0)
    if isinstance(value, bool):
        return Decimal(int(value))
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def money(value: Any) -> Decimal:
    """Деньги: HALF_UP до копеек."""
    return to_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def money_float(value: Any) -> float:
    return float(money(value))


def rub(value: Any) -> Decimal:
    """Целые рубли: HALF_UP до рубля (используется в существующей формуле)."""
    return to_decimal(value).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def rub_float(value: Any) -> float:
    return float(rub(value))


def quantity_up(value: Any, digits: int = 3) -> Decimal:
    """Количество с округлением вверх (листы, метры, м²)."""
    quantum = Decimal(1).scaleb(-digits)
    return to_decimal(value).quantize(quantum, rounding=ROUND_CEILING)


def quantity_float(value: Any, digits: int = 3) -> float:
    return float(quantity_up(value, digits))
