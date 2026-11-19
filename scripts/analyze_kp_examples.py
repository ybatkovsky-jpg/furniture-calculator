"""Разбор примеров КП (PDF) для сверки с расчётом.

Что делает:
- вытаскивает пары «наличный / безналичный» и считает фактическую наценку
  за безнал (в нашей модели сейчас 13 %);
- собирает названия позиций и материалы, чтобы видеть, чего нет в прайсе.

Результат: docs/kp_examples_2026-11-17.json + сводка в stdout.
Запуск: .\\.venv\\Scripts\\python.exe scripts/analyze_kp_examples.py
"""

from __future__ import annotations

import glob
import json
import re
from pathlib import Path

import pymupdf

KP_DIR = Path("KP")
OUT_JSON = Path("docs/kp_examples_2026-11-17.json")

NUM = r"([\d][\d\s]{2,})\s*(?:₽|р\.)"
CASH = re.compile(r"наличн[^\d]{0,60}?" + NUM, re.IGNORECASE | re.DOTALL)
NONCASH = re.compile(r"безнал[^\d]{0,60}?" + NUM, re.IGNORECASE | re.DOTALL)
TITLE = re.compile(r"^\s*(\d{1,2})\.\s*(.{3,90})$", re.MULTILINE)
# Заголовок позиции — не только «1. Кухня», но и «Кухня:», «Шкаф в прихожей»
# без нумерации. Иначе половина позиций попадает в «Прочее».
TITLE_KEYWORDS = (
    "кухн", "шкаф", "гардероб", "прихож", "спальн", "детск", "гостин",
    "ванн", "сануз", "тумб", "ресепшн", "витрин", "стеллаж",
    "комод", "кровать", "барн",
)
# Слово с начала, а не подстрока: «отверстие» не должно давать «тв»,
# «столешница» — «стол».
TITLE_RE = re.compile(
    r"(?<![а-яё])(" + "|".join(TITLE_KEYWORDS) + r")|\bтв\b|\bстол\b"
)


def _to_int(raw: str) -> int:
    return int(re.sub(r"\D", "", raw))


def _titles(text: str) -> list[tuple[int, str]]:
    titles = [
        (m.start(), f"{m.group(1)}. {m.group(2).strip()}")
        for m in TITLE.finditer(text)
    ]
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        lowered = stripped.casefold()
        if (
            3 <= len(stripped) <= 90
            and not stripped.startswith("-")
            and TITLE_RE.search(lowered)
        ):
            titles.append((offset, stripped))
        offset += len(line)
    titles.sort(key=lambda pair: pair[0])
    return titles


def _title_before(titles: list[tuple[int, str]], pos: int) -> str:
    best = ""
    for start, title in titles:
        if start < pos:
            best = title
        else:
            break
    return best


def main() -> int:
    files = sorted(glob.glob(str(KP_DIR / "*.pdf")))
    documents = []
    all_ratios: list[float] = []

    for path in files:
        doc = pymupdf.open(path)
        text = "\n".join(page.get_text() for page in doc)
        text = text.replace("\xa0", " ")
        titles = _titles(text)
        cash = [(_to_int(m.group(1)), m.start()) for m in CASH.finditer(text)]
        noncash = [(_to_int(m.group(1)), m.start()) for m in NONCASH.finditer(text)]

        pairs = []
        # Пары ищем по близости: к каждому «безнал» — ближайший предыдущий
        # «наличный» в пределах 400 символов. Иначе списки разъезжаются на
        # документах, где слово «наличный» встречается в заголовке раздела.
        for noncash_value, position in noncash:
            candidates = [
                (cash_value, cash_pos)
                for cash_value, cash_pos in cash
                if cash_pos < position and position - cash_pos <= 400
            ]
            if not candidates:
                continue
            cash_value, cash_pos = candidates[-1]
            if cash_value <= 0:
                continue
            ratio = noncash_value / cash_value
            all_ratios.append(ratio)
            pairs.append(
                {
                    "item": _title_before(titles, cash_pos),
                    "cash": cash_value,
                    "noncash": noncash_value,
                    "markup_percent": round((ratio - 1) * 100, 2),
                }
            )

        documents.append(
            {
                "file": Path(path).name,
                "pages": doc.page_count,
                "pairs": pairs,
            }
        )

    # Сводка по наценке: сколько пар на 10 %, сколько на 13 %.
    buckets: dict[str, int] = {}
    for ratio in all_ratios:
        percent = round((ratio - 1) * 100)
        key = f"{percent} %"
        buckets[key] = buckets.get(key, 0) + 1

    payload = {
        "source": "KP/*.pdf",
        "documents": documents,
        "markup_buckets": dict(sorted(buckets.items(), key=lambda kv: -kv[1])),
        "pairs_total": len(all_ratios),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"файлов: {len(documents)}, пар наличный/безнал: {len(all_ratios)}")
    print("наценка за безнал (округлённая), пар:")
    for key, count in payload["markup_buckets"].items():
        print(f"  {key:>6}: {count}")
    print(f"JSON: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
