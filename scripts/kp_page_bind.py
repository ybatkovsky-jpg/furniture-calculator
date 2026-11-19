"""Правильная привязка цен в КП: позиция → цена × количество.

Проблема прежней сверки: бралась ПЕРВАЯ цена на странице, без умножения на
«N шт». На волонтёрском центре это давало +760 % (цена столов привязана к
шкафам). Здесь считаем цену каждой позиции и её количество.

Запуск: .\\.venv\\Scripts\\python.exe scripts/kp_page_bind.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf

OUT = Path("docs/kp_page_totals.json")

# «Стоимость за 1 единицу, шт: 45 700 ₽», «Стоимость при наличной оплате: ...»
UNIT_PRICE = re.compile(
    r"(?:за 1 единицу|(?<![а-яё])наличн\w*|за наличный расчет)[^\d]{0,40}?([\d][\d\s]{2,})\s*(?:₽|р\.)",
    re.IGNORECASE | re.DOTALL,
)
QTY = re.compile(r"(\d+)\s*шт", re.IGNORECASE)


def page_items(text: str) -> list[dict]:
    """Позиции страницы: цена за единицу и количество из ближайшего «N шт».

    «Стоимость за 1 единицу» — основной вариант; «наличная оплата» берём,
    только если за единицу цены нет. «Безналичная» не матчится: в слове
    «безналичной» нет границы перед «наличн».
    """
    unit = [
        m for m in UNIT_PRICE.finditer(text)
        if m.group(0).casefold().startswith("за 1 единицу")
    ]
    cash = [m for m in UNIT_PRICE.finditer(text) if m not in unit]
    matches = unit or cash
    items = []
    for match in matches:
        price = int(re.sub(r"\D", "", match.group(1)))
        window = text[max(0, match.start() - 600):match.start()]
        qtys = [int(q) for q in QTY.findall(window)]
        qty = qtys[-1] if qtys else 1
        items.append({"unit_price": price, "qty": qty, "amount": price * qty})
    return items


def main() -> int:
    rows = []
    for path in sorted(Path("KP").glob("*.pdf")):
        doc = pymupdf.open(path)
        for index, page in enumerate(doc):
            text = page.get_text().replace("\xa0", " ")
            items = page_items(text)
            if not items:
                continue
            total = sum(item["amount"] for item in items)
            rows.append({
                "file": path.name,
                "page": index + 1,
                "items": items,
                "page_total_cash": total,
            })
            print(f"{path.name[:45]:45} p{index + 1} позиций={len(items)} "
                  f"итог={total:>9} ({', '.join(str(i['unit_price']) + 'x' + str(i['qty']) for i in items)})")

    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСтраниц с привязкой: {len(rows)} → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
