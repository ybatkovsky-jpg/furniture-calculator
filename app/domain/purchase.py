"""Закупка по раскрою (этап 4): листы и кромка в закупочных единицах.

Цены сюда не подставляются: это количество к закупке. Цена берётся из
прайса/Excel отдельно, чтобы не смешивать раскрой и деньги.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PurchaseLine:
    name: str
    unit: str
    quantity: float
    unit_price: float = 0.0
    total: float = 0.0
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PurchaseList:
    lines: list[PurchaseLine] = field(default_factory=list)

    def add(
        self,
        name: str,
        unit: str,
        quantity: float,
        source: str = "",
        unit_price: float = 0.0,
    ) -> None:
        if quantity > 0:
            self.lines.append(
                PurchaseLine(
                    name,
                    unit,
                    round(quantity, 2),
                    round(float(unit_price or 0), 2),
                    round(quantity * float(unit_price or 0), 2),
                    source,
                )
            )

    def as_dict(self) -> dict[str, Any]:
        return {"lines": [line.as_dict() for line in self.lines]}


def build_purchase_list(
    nesting: Any,
    edge_totals: dict[str, float],
    prices: dict[str, float] | None = None,
    *,
    hdf_sheets: int = 0,
    mdf_sheets: int = 0,
    facades_area_m2: float = 0.0,
) -> PurchaseList:
    """Листы по материалам + кромка по толщинам.

    `prices` — цена за единицу по имени строки; если цены нет, остаётся 0.
    """
    result = PurchaseList()
    prices = prices or {}

    sheets: dict[str, int] = {}
    for layout in getattr(nesting, "layouts", []) or []:
        material = str(getattr(layout, "material", "") or "Прочее")
        sheets[material] = sheets.get(material, 0) + 1
    for material, count in sorted(sheets.items()):
        name = f"Лист {material}"
        result.add(name, "лист", count, "nesting", prices.get(name, 0.0))

    result.add("Лист ХДФ", "лист", hdf_sheets, "quantity_calc", prices.get("Лист ХДФ", 0.0))
    if facades_area_m2 <= 0:
        result.add(
            "Лист МДФ",
            "лист",
            mdf_sheets,
            "quantity_calc",
            prices.get("Лист МДФ", 0.0),
        )
    result.add(
        "Фасады",
        "м²",
        facades_area_m2,
        "quantity_calc",
        prices.get("Фасады", 0.0),
    )

    for thickness, meters in sorted((edge_totals or {}).items()):
        name = f"Кромка {thickness} мм"
        result.add(name, "м.п.", float(meters), "edge", prices.get(name, 0.0))

    return result
