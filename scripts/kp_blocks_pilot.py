"""Пилот посубъектной сверки КП: товарный блок → своё изображение → цена.

Страница КП режется на позиции по заголовкам («Кухонный гарнитур. 3000*1700.
Высота 2550мм. 1шт»), цена берётся из сегмента позиции и умножается на «N шт».
К позиции привязываются её изображения (по вертикали и колонке), каждое
разбирается VLM отдельно, модули объединяются.

Запуск: .\\.venv\\Scripts\\python.exe scripts/kp_blocks_pilot.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services import image_analyzer
from app.services.calc_engine import calculate_full_cost
from app.services.image_analyzer import get_analyzer

PDF = Path("KP/Коммерческое предложение волонтерский центр.pdf")
OUT = Path("docs/kp_blocks_pilot.json")
TMP = Path("temp/kp_blocks")
TMP.mkdir(parents=True, exist_ok=True)

TITLE_ITEM = re.compile(
    r"([А-ЯЁ][^\n]{2,80}?)\s*(\d{3,4})\s*[*хx×]\s*(\d{2,4})\.?\s*"
    r"(?:[Вв]ысота\s*(\d{3,4})\s*мм?\.?)?\s*(\d+)\s*шт",
    re.DOTALL,
)
UNIT_PRICE = re.compile(
    r"за 1 единицу[^\d]{0,40}?([\d][\d\s]{2,})\s*(?:₽|р\.)", re.IGNORECASE | re.DOTALL
)

MATS = {
    "ldsp": {"name": "ТОМЛЕСДРЕВ белый", "price": 3600, "price_unit": "лист"},
    "facades": {"name": "ЛДСП Томлесдрев", "price": 3600, "price_unit": "лист",
                "sheet_width_mm": 2800, "sheet_height_mm": 2070},
    "edge": {"name": "EGGER 0.8*19", "price": 41, "thickness_mm": 0.8},
}
HW = {"hinges": {"price": 200, "brand": "FIRMAX"},
      "drawers": {"price": 3300}, "handles": {"price": 500}}
PB = {"hdf": 1380, "confirmat": 2, "adjustable_feet": 50, "plinth": 1500,
      "edge_0.4": 22, "countertop": 2834}


def _zone(title: str) -> str:
    text = title.casefold()
    if "кух" in text:
        return "Кухня"
    if "шкаф" in text or "стеллаж" in text:
        return "Гардеробная"
    return "Офис"


def _items(page) -> list[dict]:
    text = page.get_text().replace("\xa0", " ")
    matches = list(TITLE_ITEM.finditer(text))
    blocks = page.get_text("blocks")
    items = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        price = UNIT_PRICE.search(text[match.start():end])
        title = " ".join(match.group(0).split())
        key = title[:25]
        ys = [b[1] for b in blocks if key in " ".join(b[4].split())]
        items.append({
            "title": title[:110],
            "y0": min(ys) if ys else 0.0,
            "width": int(match.group(2)),
            "depth": int(match.group(3)),
            "height": int(match.group(4)) if match.group(4) else 0,
            "qty": int(match.group(5)),
            "price": int(re.sub(r"\D", "", price.group(1))) if price else 0,
            "images": [],
        })
    return items


def _images(doc, page) -> list[dict]:
    """Нативные изображения страницы: bbox в пунктах + пиксельный размер."""
    result = []
    seen = set()
    for info in page.get_image_info(xrefs=True):
        xref = info.get("xref")
        if not xref or xref in seen:
            continue
        seen.add(xref)
        pix = doc.extract_image(xref)
        bbox_check = info["bbox"]
        if not pix or pix["width"] < 150 or pix["height"] < 150:
            continue
        if bbox_check[3] < 200:  # шапка, логотип
            continue
        bbox = info["bbox"]
        result.append({"xref": xref, "bbox": tuple(bbox),
                       "width": pix["width"], "height": pix["height"],
                       "ext": pix["ext"], "bytes": pix["image"]})
    return result


def _attach(items, images) -> None:
    """Позиция получает изображения из своей вертикальной полосы."""
    for index, item in enumerate(items):
        next_y = items[index + 1]["y0"] if index + 1 < len(items) else 10_000
        top = item["y0"] - 25
        bottom = next_y + 5
        item["images"] = [
            image for image in images
            if top <= (image["bbox"][1] + image["bbox"][3]) / 2 <= bottom
        ][:3]


def _modules(recognition) -> list[dict]:
    return [
        {"type": m.type, "width": m.width, "depth": m.depth, "height": m.height,
         "quantity": m.quantity, "facades": m.facades, "drawers": m.drawers,
         "shelves": m.shelves, "has_glass": m.has_glass}
        for m in recognition.modules
    ]


async def main() -> int:
    doc = pymupdf.open(PDF)
    analyzer = get_analyzer()
    base_prompt = image_analyzer.UNIFIED_PROMPT_V2
    rows = []
    for page_index, page in enumerate(doc):
        items = _items(page)
        _attach(items, _images(doc, page))
        for item_index, item in enumerate(items):
            row = {
                "page": page_index + 1, "title": item["title"],
                "width": item["width"], "depth": item["depth"],
                "height": item["height"], "qty": item["qty"],
                "kp_unit": item["price"], "kp_total": item["price"] * item["qty"],
                "photos": len(item["images"]),
            }
            image_analyzer.UNIFIED_PROMPT_V2 = (
                base_prompt
                + "\n\nТЕКСТ ПОЗИЦИИ КП (габариты и количество): "
                + item["title"]
            )
            modules: list[dict] = []
            for photo_index, image in enumerate(item["images"]):
                path = TMP / f"p{page_index + 1}_i{item_index + 1}_{photo_index}.{image['ext']}"
                path.write_bytes(image["bytes"])
                recognition = await analyzer.analyze_drawing(str(path))
                if not recognition.modules:
                    pad_x = (image["bbox"][2] - image["bbox"][0]) * 0.1
                    pad_y = (image["bbox"][3] - image["bbox"][1]) * 0.1
                    clip = pymupdf.Rect(
                        image["bbox"][0] - pad_x, image["bbox"][1] - pad_y,
                        image["bbox"][2] + pad_x, image["bbox"][3] + pad_y,
                    )
                    page.get_pixmap(clip=clip, dpi=220).save(
                        TMP / f"p{page_index + 1}_i{item_index + 1}_{photo_index}_pad.png"
                    )
                    recognition = await analyzer.analyze_drawing(
                        str(TMP / f"p{page_index + 1}_i{item_index + 1}_{photo_index}_pad.png")
                    )
                modules.extend(_modules(recognition))
            # Проверка полноты: сумма ширин корпусов против габарита из КП.
            width_sum = sum(
                (module.get("width") or 0) * (module.get("quantity") or 1)
                for module in modules
            )
            target_width = item.get("width") or 0
            if (
                target_width > 0
                and modules
                and width_sum < 0.8 * target_width
                and item["images"]
            ):
                hint = (
                    "\n\nВАЖНО: суммарная ширина перечисленных корпусов "
                    f"{width_sum} мм, а габарит изделия {target_width} мм. "
                    "Перечисли ВСЕ корпуса ряда, включая верхние, пеналы и "
                    "угловые, ничего не пропуская."
                )
                image_analyzer.UNIFIED_PROMPT_V2 = (
                    base_prompt + "\n\nТЕКСТ ПОЗИЦИИ КП: " + item["title"] + hint
                )
                retry_modules: list[dict] = []
                for image in item["images"]:
                    suffix = ".png" if image["ext"] == "png" else f".{image['ext']}"
                    path = TMP / f"p{page_index + 1}_i{item_index + 1}_retry{suffix}"
                    path.write_bytes(image["bytes"])
                    recognition = await analyzer.analyze_drawing(str(path))
                    retry_modules.extend(_modules(recognition))
                if retry_modules:
                    modules = retry_modules
                    row["retry"] = True

            unique, seen = [], set()
            for module in modules:
                signature = (
                    module["type"], module["width"], module["depth"], module["height"],
                    (module["facades"] or {}).get("count", 0),
                    (module["drawers"] or {}).get("count", 0),
                )
                if signature in seen:
                    continue
                seen.add(signature)
                unique.append(module)
            modules = unique
            row["modules"] = len(modules)
            our = 0
            if modules:
                result = calculate_full_cost(
                    modules, MATS, HW, [], zone_type=_zone(item["title"]),
                    price_book=PB,
                )
                our = round(result.total_cash)
            row["our"] = our
            base = row["kp_total"]
            row["baseline"] = base
            row["delta"] = round((our - base) / base * 100, 1) if base else None
            rows.append(row)
            print(f"p{row['page']} {item['title'][:42]:42} фото={row['photos']} "
                  f"КП {row['kp_unit']}×{row['qty']}={row['kp_total']:>8} "
                  f"наш={our:>8} Δ={row['delta']}% mods={len(modules)}")

    image_analyzer.UNIFIED_PROMPT_V2 = base_prompt
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nПозиций: {len(rows)} → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
