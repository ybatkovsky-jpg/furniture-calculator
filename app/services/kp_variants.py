"""Варианты коммерческого предложения: базовая / стандарт / комфорт.

Один и тот же состав считается трижды — меняются материалы и фурнитура.
Ступени заданы **по назначению позиции**, а не по цене: в прайсе есть стеновые
панели и подъёмники, и «самая дорогая ЛДСП» оказывается панелью 600×4200×8.

Расчёт каждого варианта идёт через `calculate_full_cost` — это полноценная
смета, а не наценка на базовую.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.calc_engine import calculate_full_cost
from app.services.price_book import load_price_book_sync

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "furniture.db"

CATEGORIES = {
    "ldsp": ("ЛДСП",),
    "edge": ("Кромка",),
    "facades": ("Фасады", "МДФ плиты", "МДФ", "ЭФТРИ ПВХ", "Алюминиевый фасад"),
    "hinges": ("Петли",),
    "drawers": ("Ящики",),
    "handles": ("Ручки",),
}

# Что не может быть основным материалом варианта.
EXCLUDE = (
    "стеновая", "подъемник", "подъёмник", "сушка", "лоток", "поддон",
    "профиль", "холодильник", "узкой алюм", "для стекла", "угловая", "петля-",
)

# Ступени: (название, описание, правила поиска по категориям).
# Правило — кортеж подстрок; первое совпадение выигрывает, иначе берётся
# медиана диапазона (для стандарта) или край (для базы/комфорта).
TIERS: tuple[tuple[str, str, dict[str, tuple[str, ...]]], ...] = (
    (
        "Базовая",
        "Практичное решение: надёжные материалы без переплаты",
        {
            "ldsp": ("томлесдрев", "extravert"),
            "facades": ("пвх", "ivego"),
            "hinges": ("), "),
            "drawers": ("старт",),
            "handles": ("ручка торцевая",),
            "edge": ("extravert", "evo"),
        },
    ),
    (
        "Стандарт",
        "Оптимальный выбор: износостойкие фасады и доводчики",
        {
            "ldsp": ("lamarty", "slotex 3000"),
            "facades": ("акриловые 18мм матовый", "акрил"),
            "hinges": ("боярд evo", "blum накладная", "блюм", "боярд"),
            "drawers": ("avantech", "innotech"),
            "handles": ("ручка торцевая",),
            "edge": ("egger", "agt"),
        },
    ),
    (
        "Комфорт",
        "Премиум: акрил или эмаль, фурнитура Blum / LEGRABOX",
        {
            "ldsp": ("egger", "lamarty"),
            "facades": ("глянец металлик", "глянец", "акрил"),
            "hinges": ("blum 155", "blum накладная", "hettich", "blum"),
            "drawers": ("legrabox", "avantech you"),
            "handles": ("ручка торцевая",),
            "edge": ("supramat", "agt", "мдф"),
        },
    ),
)

VARIANT_IDS = ("base", "standard", "comfort")


@dataclass
class Variant:
    """Один вариант КП."""

    name: str
    description: str
    materials_text: str
    final_cash: float
    final_noncash: float
    cost_price: float
    modules: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    id: str = ""


def _catalog() -> dict[str, list[dict]]:
    """Активные позиции прайса по нужным категориям, по возрастанию цены."""
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        catalog: dict[str, list[dict]] = {}
        for key, categories in CATEGORIES.items():
            placeholders = ",".join("?" for _ in categories)
            rows = connection.execute(
                f"select * from price_items where is_active=1 and unit_price is not null "
                f"and category in ({placeholders}) order by unit_price asc",
                categories,
            ).fetchall()
            items = [
                dict(row)
                for row in rows
                if not any(word in (row["name"] or "").lower() for word in EXCLUDE)
            ]
            # Фасады в движке считаются за м² (`_calculate_facades_cost` берёт
            # цену как цену квадратного метра). Позиции «за лист» и «за метр»
            # сюда не годятся — иначе цена листа умножается на м².
            if key == "facades":
                items = [item for item in items if item["price_unit"] == "м²"]
            catalog[key] = items
        return catalog
    finally:
        connection.close()


def _pick(items: list[dict], patterns: tuple[str, ...], share: float) -> dict | None:
    """Позиция по правилу; если правила нет — по ценовой доле диапазона."""
    if not items:
        return None
    for pattern in patterns:
        if pattern == "), ":
            # «самая дешёвая» — отдельное правило без текстового совпадения.
            return items[0]
        for item in items:
            if pattern in (item["name"] or "").lower():
                return item
    index = min(len(items) - 1, max(0, round((len(items) - 1) * share)))
    return items[index]


def _materials_for(
    catalog: dict[str, list[dict]], rules: dict[str, tuple[str, ...]], share: float
) -> tuple[dict, dict, str]:
    """Материалы, фурнитура и строка «что входит» для варианта."""
    ldsp = _pick(catalog["ldsp"], rules["ldsp"], share)
    edge = _pick(catalog["edge"], rules["edge"], share)
    facade = _pick(catalog["facades"], rules["facades"], share)
    hinges = _pick(catalog["hinges"], rules["hinges"], share)
    drawers = _pick(catalog["drawers"], rules["drawers"], share)
    handles = _pick(catalog.get("handles", []), rules.get("handles", ()), share)

    materials: dict[str, Any] = {}
    if ldsp:
        materials["ldsp"] = {
            "id": ldsp["id"],
            "name": ldsp["name"],
            "price": ldsp["unit_price"],
            "price_unit": ldsp["price_unit"],
            "thickness_mm": ldsp["thickness_mm"],
            "brand": ldsp["brand"],
        }
    if edge:
        materials["edge"] = {
            "id": edge["id"],
            "name": edge["name"],
            "price": edge["unit_price"],
        }
    if facade:
        materials["facades"] = {
            "id": facade["id"],
            "name": facade["name"],
            "price": facade["unit_price"],
            "price_unit": facade["price_unit"],
        }

    hardware: dict[str, Any] = {}
    if hinges:
        hardware["hinges"] = {
            "id": hinges["id"],
            "name": hinges["name"],
            "price": hinges["unit_price"],
        }
    if drawers:
        hardware["drawers"] = {
            "id": drawers["id"],
            "name": drawers["name"],
            "price": drawers["unit_price"],
        }
    if handles:
        hardware["handles"] = {
            "id": handles["id"],
            "name": handles["name"],
            "price": handles["unit_price"],
        }

    parts = [
        f"Корпус: {ldsp['name']}" if ldsp else "",
        f"Фасады: {facade['name']}" if facade else "",
        f"Кромка: {edge['name']}" if edge else "",
        f"Петли: {hinges['name']}" if hinges else "",
        f"Ящики: {drawers['name']}" if drawers else "",
    ]
    return materials, hardware, "; ".join(part for part in parts if part)


def build_variants(
    modules: list[dict],
    *,
    zone_type: str | None = None,
    options: dict | None = None,
    discount_percent: float = 0.0,
    catalog: dict[str, list[dict]] | None = None,
    price_book: dict[str, float] | None = None,
) -> list[Variant]:
    """Посчитать варианты КП по одному и тому же составу.

    `catalog` можно передать готовым — тестам не нужна рабочая БД.
    """
    catalog = catalog if catalog is not None else _catalog()
    if price_book is None:
        try:
            # ХДФ, крепёж, цоколь, LED, подъёмники, ручки, столешница: без этого
            # набора эти строки уходили в ноль и занижали смету.
            price_book = load_price_book_sync()
        except Exception:  # noqa: BLE001 — без прайса считаем как раньше
            price_book = {}
    variants: list[Variant] = []

    for index, (name, description, rules) in enumerate(TIERS):
        share = (0.0, 0.5, 1.0)[index]
        materials, hardware, materials_text = _materials_for(catalog, rules, share)
        if not materials.get("ldsp"):
            continue
        result = calculate_full_cost(
            modules,
            materials,
            hardware,
            [],
            zone_type=zone_type,
            options=options or None,
            price_book=price_book,
        )
        breakdown = result.cost_breakdown
        cash = float(breakdown.final_cash)
        # Наценку безнала берём отношением из расчёта — политика уже применена.
        ratio = (float(result.total_noncash) / cash) if cash else 1.0
        if discount_percent > 0:
            cash = round(cash * (1 - discount_percent / 100), 2)
        noncash = round(cash * ratio, 2)
        variants.append(
            Variant(
                name=name,
                description=description,
                materials_text=materials_text,
                final_cash=cash,
                final_noncash=noncash,
                cost_price=float(breakdown.cost_price),
                modules=modules,
                warnings=list(result.warnings or []),
                id=VARIANT_IDS[index],
            )
        )
    return variants
