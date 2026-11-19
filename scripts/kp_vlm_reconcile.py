"""Пакетная сверка КП: страница → VLM → модули → цена конвейера → цена КП.

Для каждой страницы каждого КП:
1. берём цену наличными из текста страницы;
2. рендерим страницу в PNG;
3. прогоняем VLM (`analyze_drawing`) → модули;
4. считаем `calculate_full_cost` с материалами, угаданными по тексту страницы;
5. сравниваем с ценой КП.

Результат: docs/kp_vlm_results.json (пишется после каждой страницы) и сводка.

Запуск: .\\.venv\\Scripts\\python.exe scripts/kp_vlm_reconcile.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.calc_engine import calculate_full_cost
from app.services.image_analyzer import get_analyzer

OUT = Path("docs/kp_vlm_results.json")
TMP = Path("temp/kp_vlm_pages")
TMP.mkdir(parents=True, exist_ok=True)

CASH = re.compile(r"(?:наличн|за 1 единицу)[^\d]{0,60}?([\d][\d\s]{2,})\s*(?:₽|р\.)", re.IGNORECASE | re.DOTALL)
NONCASH = re.compile(r"безнал[^\d]{0,60}?([\d][\d\s]{2,})\s*(?:₽|р\.)", re.IGNORECASE | re.DOTALL)

BRANDS = {
    "LAMARTY": 4500,
    "EXTRAVERT": 5650,
    "EGGER": 6000,
    "ТОМЛЕСДРЕВ": 3600,
}
FACADES = {
    "EVOSOFT": (10200, 2800, 1220),
    "EVOGLOSS": (6200, 2800, 1220),
    "EMDIWAY": (9800, 2800, 2070),
    "EMDIWEY": (9800, 2800, 2070),
    "МДФ": (10200, 2800, 1220),
}
PB = {"hdf": 1380, "confirmat": 2, "adjustable_feet": 50, "plinth": 1500, "edge_0.4": 22}


def _num(raw: str) -> int:
    return int(re.sub(r"\D", "", raw))


def _materials(text: str) -> dict:
    upper = text.upper()
    ldsp_price = 6000
    for brand, price in BRANDS.items():
        if brand in upper:
            ldsp_price = price
            break
    fac_price, fw, fh = 10200, 2800, 1220
    for brand, (price, w, h) in FACADES.items():
        if brand in upper:
            fac_price, fw, fh = price, w, h
            break
    return {
        "ldsp": {"name": "ЛДСП", "price": ldsp_price, "price_unit": "лист"},
        "facades": {
            "name": "Фасад", "price": fac_price, "price_unit": "лист",
            "sheet_width_mm": fw, "sheet_height_mm": fh,
        },
        "edge": {"name": "EGGER 0.8*19", "price": 41, "thickness_mm": 0.8},
    }


async def main() -> int:
    analyzer = get_analyzer()
    results = []
    for path in sorted(Path("KP").glob("*.pdf")):
        doc = pymupdf.open(path)
        for index, page in enumerate(doc):
            text = page.get_text()
            cash_match = CASH.search(text)
            price = _num(cash_match.group(1)) if cash_match else 0
            image_path = TMP / f"{path.stem[:40]}_{index + 1}.png"
            page.get_pixmap(dpi=110).save(image_path)

            try:
                recognition = await analyzer.analyze_drawing(str(image_path))
            except Exception as error:  # noqa: BLE001
                print(f"{path.name} p{index + 1}: VLM error {error}")
                continue

            modules = [
                {
                    "type": m.type, "width": m.width, "depth": m.depth,
                    "height": m.height, "quantity": m.quantity,
                    "facades": m.facades, "drawers": m.drawers, "shelves": m.shelves,
                    "has_glass": m.has_glass,
                }
                for m in recognition.modules
            ]
            our = 0
            if modules:
                result = calculate_full_cost(
                    modules,
                    _materials(text),
                    {"hinges": {"price": 200, "brand": "FIRMAX"},
                     "drawers": {"price": 3300}, "handles": {"price": 500}},
                    [],
                    zone_type="Кухня",
                    price_book=PB,
                )
                our = round(result.cost_breakdown.furniture_cash)

            row = {
                "file": path.name,
                "page": index + 1,
                "kp_cash": price,
                "our_cash": our,
                "modules": modules,
                "confidence": recognition.confidence,
                "delta_percent": round((our - price) / price * 100, 1) if price else None,
            }
            results.append(row)
            OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{path.name[:45]:45} p{index + 1} КП={price:>8} наш={our:>8} "
                  f"Δ={row['delta_percent']}% mods={len(modules)}")

    print(f"\nВсего страниц: {len(results)} → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
