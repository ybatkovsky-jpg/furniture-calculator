"""Единый каталог цен для Excel и чата (День 2 плана исправления).

Раньше Excel читал прайс напрямую из SQLite (`template_filler._load_db_prices`),
только по ключевым словам и без проверки категории/бренда/толщины. Из-за этого
часть строк оставалась с ценой шаблона, а часть получала цену другой позиции.

Здесь один загрузчик (`load_price_catalog`) и одно правило подбора
(`find_price`). Подбор консервативный: если подходящая цена не единственная —
возвращается `None`, позиция уходит в «⚠ Проблемы», а не получает
правдоподобную, но чужую цену.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.price import PriceItem
from app.services.price_manager import normalize_text

# Единицы, которые считаются одной и той же в шаблоне и в прайсе.
_UNIT_ALIASES = {
    "м²": "м²",
    "м2": "м²",
    "кв.м": "м²",
    "кв.м.": "м²",
    "м": "м",
    "м.п.": "м",
    "м.п": "м",
    "пог.м": "м",
    "пог.м.": "м",
    "шт": "шт",
    "штук": "шт",
    "шт.": "шт",
    "лист": "лист",
    "листов": "лист",
    "лст": "лист",
    "комплект": "комплект",
    "компл": "комплект",
    "кг": "кг",
}


def normalize_unit(unit: str | None) -> str:
    text = str(unit or "").strip().lower().replace(" ", "")
    return _UNIT_ALIASES.get(text, text)


@dataclass(frozen=True)
class PriceCatalogItem:
    """Позиция прайса в виде, пригодном для Excel без ORM-сессии."""

    id: int
    name: str
    category: str
    brand: str | None
    price: float
    unit: str
    thickness_mm: float | None
    sku: str | None = None

    @property
    def sku_or_id(self) -> str:
        return self.sku or f"ID-{self.id}"

    @property
    def text(self) -> str:
        return normalize_text(" ".join(filter(None, (self.name, self.category, self.brand))))


@dataclass(frozen=True)
class PriceMatch:
    item: PriceCatalogItem
    score: float


async def load_price_catalog(session: AsyncSession) -> list[PriceCatalogItem]:
    """Загрузить активные позиции прайса с ценой (без ручных цен)."""
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
    return [
        PriceCatalogItem(
            id=item.id,
            name=item.name or "",
            category=item.category or "",
            brand=item.brand,
            price=float(item.unit_price or 0),
            unit=item.price_unit or "",
            thickness_mm=item.thickness_mm,
            sku=item.sku,
        )
        for item in result.scalars().all()
    ]


def _candidates(
    catalog: Iterable[PriceCatalogItem],
    unit: str | None,
    category: str | None,
    brand: str | None,
    thickness: float | None,
) -> list[PriceCatalogItem]:
    wanted_unit = normalize_unit(unit)
    wanted_category = normalize_text(category) if category else ""
    wanted_brand = normalize_text(brand) if brand else ""
    result = []
    for item in catalog:
        if wanted_unit and normalize_unit(item.unit) != wanted_unit:
            continue
        if wanted_category and normalize_text(item.category) != wanted_category:
            continue
        if wanted_brand and normalize_text(item.brand or "") != wanted_brand:
            continue
        if thickness is not None and item.thickness_mm != thickness:
            continue
        result.append(item)
    return result


def _match(keys: list[str], item: PriceCatalogItem) -> int:
    return sum(1 for key in keys if key in item.text)


def find_price(
    catalog: Iterable[PriceCatalogItem],
    keywords: list[str],
    unit: str | None = None,
    *,
    category: str | None = None,
    brand: str | None = None,
    thickness: float | None = None,
) -> PriceMatch | None:
    """Подобрать единственную цену; при неоднозначности вернуть None.

    Правила:
    1. сначала — все ключевые слова в названии/категории/бренде;
    2. если таких нет — достаточно первого (главного) слова и половины
       остальных, но только когда кандидат единственный по цене;
    3. разные цены у нескольких кандидатов = «не определено», а не догадка.
    """
    items = _candidates(catalog, unit, category, brand, thickness)
    keys = [key for key in (normalize_text(k) for k in keywords) if key]
    if not items or not keys:
        return None

    full = [item for item in items if _match(keys, item) == len(keys)]
    if full:
        return _unique(full, keys)

    # Частичное совпадение: главное слово обязательно, остальных — не меньше
    # половины. Это покрывает шаблонные уточнения («однотон», «МФ-ГРУПП»),
    # которых нет в прайсе. Но только если кандидат ровно один: иначе это
    # догадка, а не цена.
    main_key = keys[0]
    partial = [
        item
        for item in items
        if main_key in item.text and _match(keys, item) >= max(1, (len(keys) + 1) // 2)
    ]
    if len(partial) == 1:
        item = partial[0]
        return PriceMatch(item=item, score=_match(keys, item) / max(len(keys), 1))
    return None


def _unique(items: list[PriceCatalogItem], keys: list[str]) -> PriceMatch | None:
    """Выбрать цену, только если она однозначна.

    Точное совпадение названия с ключевыми словами побеждает «похожие»
    позиции (например, «… LEGRABOX» против «… LEGRABOX TIP ON»). Если точного
    названия нет, а цены у кандидатов разные — это неоднозначность, возвращаем
    None: пусть оператор выберет позицию, а не получит чужую цену.
    """
    joined = " ".join(keys)
    exact = [item for item in items if normalize_text(item.name) == joined]
    preferred = exact or items
    prices = {round(item.price, 2) for item in preferred}
    if len(prices) > 1:
        return None
    best = min(preferred, key=lambda item: item.id)
    return PriceMatch(item=best, score=_match(keys, best) / max(len(keys), 1))
