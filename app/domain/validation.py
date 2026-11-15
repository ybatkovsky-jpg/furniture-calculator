"""Валидация финансовых входов (P0-08).

До этого модуля `calculate_cost` принимал любые числа: скидка 1 000 000 ₽ при
цене 1 500 ₽ давала −984 852 ₽, ставка бонуса 1.0 — ZeroDivisionError,
отрицательная стоимость материала проходила в итог. Проверки собраны здесь,
чтобы чат, Excel и КП валидировали одинаково.
"""

from __future__ import annotations

import math
from collections.abc import Iterable


class PricingError(ValueError):
    """Недопустимый финансовый вход. Текст показывается оператору."""


def _ensure_finite_non_negative(name: str, value: float) -> None:
    if value is None:
        return
    if not math.isfinite(float(value)):
        raise PricingError(f"{name}: значение должно быть конечным числом")
    if float(value) < 0:
        raise PricingError(f"{name}: значение не может быть отрицательным ({value})")


def validate_cost_components(components: Iterable[tuple[str, float]]) -> None:
    """Проверить стоимости компонентов себестоимости."""
    for name, value in components:
        _ensure_finite_non_negative(name, value)


def validate_discount_inputs(
    discount_type: str | None,
    discount_value: float,
    markup_type: str | None,
    markup_value: float,
    designer_bonus_rate: float,
) -> None:
    """Проверить тип/значение скидки, наценки и ставки бонуса."""
    allowed_types = {None, "", "percent", "fixed"}
    if discount_type not in allowed_types:
        raise PricingError(f"Неизвестный тип скидки: {discount_type!r}")
    if markup_type not in allowed_types:
        raise PricingError(f"Неизвестный тип наценки: {markup_type!r}")

    discount_value = float(discount_value or 0)
    markup_value = float(markup_value or 0)

    if discount_type == "percent" and discount_value > 100:
        raise PricingError("Скидка не может быть больше 100%")
    if markup_type == "percent" and markup_value > 100:
        # Ограничение выбрано консервативно: +100% уже требует ручного
        # подтверждения собственника, а не «тихой» доплаты.
        raise PricingError("Наценка не может быть больше 100%")

    _ensure_finite_non_negative("Скидка", discount_value)
    _ensure_finite_non_negative("Наценка", markup_value)

    has_discount = bool(discount_type) and discount_value > 0
    has_markup = bool(markup_type) and markup_value > 0
    if has_discount and has_markup:
        raise PricingError(
            "Нельзя применять скидку и наценку одновременно: выберите одно"
        )

    rate = float(designer_bonus_rate or 0)
    if not math.isfinite(rate) or rate < 0 or rate >= 1:
        raise PricingError(
            "Ставка бонуса дизайнера должна быть в диапазоне 0 ≤ ставка < 1"
        )


def validate_discount_against_base(
    discount_type: str | None,
    discount_value: float,
    markup_type: str | None,
    markup_value: float,
    total_base: float,
) -> None:
    """Проверить, что скидка не превышает базу и итог не отрицателен."""
    if total_base < 0:
        raise PricingError("Базовая цена не может быть отрицательной")
    if discount_type == "fixed" and float(discount_value or 0) > total_base:
        raise PricingError(
            f"Скидка {discount_value:,.2f} ₽ больше суммы {total_base:,.2f} ₽"
        )
    if markup_type == "fixed" and float(markup_value or 0) < 0:
        raise PricingError("Наценка не может быть отрицательной")
