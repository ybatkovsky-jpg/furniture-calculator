"""Подбор цен из прайса для полного CostBreakdown (P0-02).

`selected_materials`/`selected_hardware` содержат только ЛДСП, фасады, кромку,
петли и ящики. ХДФ, GOLA, LED, столешница и крепёж раньше не тарифицировались
вообще. Здесь они подбираются из активных позиций прайса по категории/названию
с проверкой единицы измерения. Если подходящей цены нет — ключ отсутствует,
и `build_cost_lines` помечает строку как «цена не задана».
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.price import PriceItem

# key → (наборы синонимов, ожидаемая единица или None)
PRICE_BOOK_RULES: dict[str, tuple[tuple[tuple[str, ...], ...], str | None]] = {
    "hdf": ((("хдф",), ("лхдф",)), "лист"),
    "gola_horizontal": ((("gola", "гола"), ("горизонт",)), "шт"),
    "gola_vertical": ((("gola", "гола"), ("вертик",)), "шт"),
    "led_strip": ((("led", "подсвет"), ("бухт", "лент", "полос")), "м"),
    "led_power": ((("блок питания", "драйвер"),), "шт"),
    "led_sensor": ((("датчик", "сенсор"),), "шт"),
    "countertop": ((("столешниц",),), "м"),
    "confirmat": ((("конфирмат",),), "шт"),
    "adjustable_feet": ((("опора",), ("регулир",)), "шт"),
    "wall_mount": ((("подвес",),), "шт"),
    "plinth": ((("цокол",),), "шт"),
    "handles": ((("ручк",),), "шт"),
}


def _text(item: PriceItem) -> str:
    return f"{item.name or ''} {item.category or ''}".casefold()


def _matches(item: PriceItem, groups: tuple[tuple[str, ...], ...]) -> bool:
    text = _text(item)
    return all(any(term in text for term in group) for group in groups)


async def load_price_book(session: AsyncSession) -> dict[str, float]:
    """Собрать цены для строк, которых нет в выбранных материалах.

    Возвращает {ключ: цена}. Единица измерения проверяется: цена столешницы за
    м² не должна попасть в строку «за метр».
    """
    result = await session.execute(
        select(PriceItem)
        .where(
            PriceItem.is_active == 1,
            PriceItem.unit_price.is_not(None),
            PriceItem.unit_price > 0,
            PriceItem.requires_manual_price == 0,
        )
        .order_by(PriceItem.id)
    )
    items = result.scalars().all()

    book: dict[str, float] = {}
    for key, (groups, expected_unit) in PRICE_BOOK_RULES.items():
        for item in items:
            if not _matches(item, groups):
                continue
            unit = (item.price_unit or "").strip().lower()
            if expected_unit and unit and unit != expected_unit:
                continue
            book[key] = float(item.unit_price)
            break

    # Кромка МДФ 1 мм — отдельное поле толщины (P0-07), а не подстрока названия.
    for item in items:
        if "кромк" not in _text(item):
            continue
        thickness = getattr(item, "thickness_mm", None)
        try:
            thickness_value = float(thickness) if thickness is not None else None
        except (TypeError, ValueError):
            thickness_value = None
        if thickness_value == 1.0:
            book["edge_mdf_1mm"] = float(item.unit_price)
            break

    return book


def build_price_book(items: Iterable[Any]) -> dict[str, float]:
    """Синхронный вариант для тестов: items — объекты с name/category/price."""
    class _Item:
        def __init__(self, raw: Any):
            self.name = getattr(raw, "name", "")
            self.category = getattr(raw, "category", "")
            self.unit_price = getattr(raw, "unit_price", None)
            self.price_unit = getattr(raw, "price_unit", "")
            self.thickness_mm = getattr(raw, "thickness_mm", None)

    dummy = [_Item(i) for i in items]
    book: dict[str, float] = {}
    for key, (groups, expected_unit) in PRICE_BOOK_RULES.items():
        for item in dummy:
            if item.unit_price is None or float(item.unit_price) <= 0:
                continue
            if not _matches(item, groups):
                continue
            unit = (item.price_unit or "").strip().lower()
            if expected_unit and unit and unit != expected_unit:
                continue
            book[key] = float(item.unit_price)
            break
    return book
