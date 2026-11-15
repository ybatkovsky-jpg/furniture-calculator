"""Каталог материалов и фурнитуры с атрибутами (S5-02).

Сейчас прайс — плоский список `PriceItem`. Для раскроя и подбора нужны
атрибуты: вид (ЛДСП/МДФ/кромка/фасад/стекло/столешница), толщина, формат
листа, единица. Здесь прайс превращается в каталог, не меняя сами цены.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Форматы листов (мм) — те же, что в quantity_calc, без «второго набора».
SHEET_FORMATS: dict[str, tuple[int, int]] = {
    "EGGER": (2750, 1830),
    "EXTRAVERT": (2750, 1830),
    "LAMARTY": (2800, 2070),
    "EMDIWAY": (2800, 2070),
    "МДФ": (2800, 1220),
    "ХДФ": (2800, 1220),
    "СТЕКЛО": (2000, 3300),
}

CATEGORY_KIND = {
    "ЛДСП": "ldsp",
    "МДФ": "mdf",
    "МДФ плиты": "mdf",
    "ХДФ": "hdf",
    "Кромка": "edge",
    "Фасады": "facade",
    "ЭФТРИ ПВХ": "facade",
    "Лакокраска": "facade",
    "Алюминиевый фасад": "facade",
    "Стекло": "glass",
    "Столешницы": "countertop",
    "GOLA": "hardware",
    "Петли": "hardware",
    "Ящики": "hardware",
    "Направляющие": "hardware",
    "Подъёмники": "hardware",
    "Ручки": "hardware",
    "Крепёж": "hardware",
    "Подсветка": "lighting",
    "Профили": "profile",
    "Комплектующие": "accessory",
    "Сушки": "accessory",
    "Бутылочницы": "accessory",
}


@dataclass(frozen=True)
class CatalogItem:
    """Позиция каталога с нормализованными атрибутами."""

    name: str
    kind: str                 # ldsp | mdf | hdf | edge | facade | glass | ...
    category: str
    brand: str = ""
    thickness_mm: float | None = None
    sheet_width_mm: int | None = None
    sheet_height_mm: int | None = None
    unit: str = "шт"
    price: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Catalog:
    items: list[CatalogItem] = field(default_factory=list)

    def by_kind(self, kind: str) -> list[CatalogItem]:
        return [item for item in self.items if item.kind == kind]

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in self.items:
            result[item.kind] = result.get(item.kind, 0) + 1
        return result

    def price(self, kind: str, name_contains: str = "") -> float | None:
        needle = name_contains.casefold()
        for item in self.by_kind(kind):
            if item.price is not None and (not needle or needle in item.name.casefold()):
                return item.price
        return None


def _sheet_format(brand: str, kind: str) -> tuple[int | None, int | None]:
    if kind == "ldsp":
        return SHEET_FORMATS.get(brand.upper(), SHEET_FORMATS["LAMARTY"])
    if kind in ("mdf", "hdf"):
        return SHEET_FORMATS["МДФ"]
    if kind == "glass":
        return SHEET_FORMATS["СТЕКЛО"]
    return None, None


def build_catalog(price_items: list[Any]) -> Catalog:
    """Построить каталог из активных PriceItem.

    Ожидаются объекты с полями name/category/brand/unit_price/price_unit/
    thickness_mm (SQLAlchemy PriceItem или совместимые).
    """
    items: list[CatalogItem] = []
    for row in price_items:
        category = str(getattr(row, "category", "") or "")
        kind = CATEGORY_KIND.get(category)
        if kind is None:
            continue
        name = str(getattr(row, "name", "") or "")
        brand = str(getattr(row, "brand", "") or "")
        thickness = getattr(row, "thickness_mm", None)
        sheet_w, sheet_h = _sheet_format(brand, kind)
        price = getattr(row, "unit_price", None)
        items.append(
            CatalogItem(
                name=name,
                kind=kind,
                category=category,
                brand=brand,
                thickness_mm=float(thickness) if thickness is not None else None,
                sheet_width_mm=sheet_w,
                sheet_height_mm=sheet_h,
                unit=str(getattr(row, "price_unit", "") or "шт"),
                price=float(price) if price is not None else None,
                attributes={"subcategory": getattr(row, "subcategory", None)},
            )
        )
    return Catalog(items)
