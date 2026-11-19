"""Бенчмарк цен по реальным КП (вариант 2: статистика без состава модулей).

Читает docs/kp_examples_2026-11-17.json (разбор 22 PDF) и считает коридоры
цен по типам мебели: сколько позиций, медиана, 10-й и 90-й процентили.

Ограничение: в КП нет состава модулей (двери/ящики/размеры), поэтому это
ориентир по уровню цен, а не построчная сверка расчёта.

Запуск: .\\.venv\\Scripts\\python.exe scripts/kp_benchmark.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SRC = Path("docs/kp_examples_2026-11-17.json")
OUT_MD = Path("docs/kp_benchmark_2026-11-17.md")

RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Кухня", ("кухн",)),
    ("Гардеробная", ("гардероб",)),
    ("Шкаф", ("шкаф",)),
    ("Прихожая", ("прихож",)),
    ("Спальня", ("спальн",)),
    ("Детская", ("детск",)),
    ("Гостиная", ("гостин",)),
    ("Ванная / санузел", ("ванн", "сануз")),
    ("ТВ-зона / тумба", ("тв", "тумб")),
    ("Стол / ресепшн", ("стол", "ресепшн")),
    ("Витрина", ("витрин",)),
)


def classify(title: str) -> str:
    """Тип мебели по НАЧАЛУ заголовка, а не по любому слову внутри.

    Иначе «Столешница Кедр» попадает в «Стол», а «Гигиенический поддон
    в моечный блок» — в «Ванную».
    """
    head = re.sub(r"^\d+\.\s*", "", title).casefold().strip()
    head = head[:45]
    if head.startswith("столешн"):
        return "Столешница"
    words = re.findall(r"[а-яё]+", head)[:3]
    for label, keys in RULES:
        for key in keys:
            if head.startswith(key) or key in words:
                return label
    return "Прочее"


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def main() -> int:
    payload = json.loads(SRC.read_text(encoding="utf-8"))
    items = [pair for doc in payload["documents"] for pair in doc["pairs"]]
    items = [p for p in items if p["item"] and p["cash"] > 0]

    groups: dict[str, list[int]] = {}
    for pair in items:
        title = re.sub(r"^\d+\.\s*", "", pair["item"])
        groups.setdefault(classify(title), []).append(int(pair["cash"]))

    rows = []
    for label, values in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        rows.append(
            {
                "label": label,
                "count": len(values),
                "min": min(values),
                "p10": percentile(values, 0.10),
                "median": percentile(values, 0.50),
                "p90": percentile(values, 0.90),
                "max": max(values),
            }
        )

    total = len(items)
    buckets = {
        "до 100 тыс.": sum(1 for p in items if p["cash"] < 100_000),
        "100–300 тыс.": sum(1 for p in items if 100_000 <= p["cash"] < 300_000),
        "300 тыс.–1 млн": sum(1 for p in items if 300_000 <= p["cash"] < 1_000_000),
        "от 1 млн": sum(1 for p in items if p["cash"] >= 1_000_000),
    }

    lines = [
        "# Бенчмарк цен по реальным КП (2026-11-17)",
        "",
        (
            f"Источник: `KP/*.pdf` — {len(payload['documents'])} файлов, "
            f"{payload['pairs_total']} пар «наличный/безнал»; в бенчмарк вошли "
            f"**{total}** позиций с названием и ценой."
        ),
        "",
        (
            "Ограничение: в КП нет состава модулей, поэтому таблица показывает "
            "**уровень цен по типам мебели**, а не сверку конкретного расчёта."
        ),
        "",
        "## Коридоры цен (наличный расчёт, ₽)",
        "",
        "| Тип | Позиций | Мин | 10 % | Медиана | 90 % | Макс |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['label']} | {row['count']} | {row['min']:,} | {row['p10']:,} | "
            f"{row['median']:,} | {row['p90']:,} | {row['max']:,} |".replace(",", " ")
        )

    lines += [
        "",
        "## Распределение по чеку",
        "",
        "| Диапазон | Позиций | Доля |",
        "|---|---:|---:|",
    ]
    for label, count in buckets.items():
        share = count / total * 100 if total else 0
        lines.append(f"| {label} | {count} | {share:.0f} % |")

    lines += [
        "",
        "## Что это даёт",
        "",
        "- медиана кухни — ориентир для проверки нашей сметы на типовом составе;",
        (
            "- разброс (10–90 %) показывает, насколько цена зависит от состава "
            "и материалов, а не только от метража;"
        ),
        "- позиции дешевле 100 тыс. — в основном прихожие, ТВ-тумбы, отдельные шкафы.",
        "",
        "Пересчёт: `scripts/kp_benchmark.py`.",
    ]

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))
    print(f"\nСохранено: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
