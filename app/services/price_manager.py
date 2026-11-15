"""Импорт прайса из XLSX и нормализация позиций.

P0-01: цена берётся ТОЛЬКО из ячейки цены. Раньше при пустой цене код
перебирал всю строку и брал первое положительное число — включая цифры имени
(«LAMARTY 0,4*19» → 0,419 ₽ за лист). Такая цена занижала себестоимость в
тысячи раз.

P0-07: толщина кромки — отдельное поле `PriceItem.thickness_mm`, извлекается
явным шаблоном названия, а не поиском подстроки «2».

P1-16: история цен пишется по факту изменения (сравнение ДО присваивания).
"""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.price import PriceHistory, PriceItem

# ── Категории ───────────────────────────────────────────────────────────────
# Специфичные маркеры важнее общих: «КРОМКА К ЛДСП» — это кромка, а не ЛДСП.
# Формат: (маркер в нормализованном тексте, категория, приоритет).
CATEGORY_RULES: tuple[tuple[str, str, int], ...] = (
    # кромка — специфичные сочетания
    ("кромка к лдсп", "Кромка", 100),
    ("кромка к мдф", "Кромка", 100),
    ("кромка пвх", "Кромка", 100),
    ("кромка abs", "Кромка", 100),
    ("кромка мдф", "Кромка", 100),
    ("кромк", "Кромка", 50),
    # фасады и плиты
    ("алюминиев", "Алюминиевый фасад", 60),
    ("эфтри", "ЭФТРИ ПВХ", 60),
    ("пвх", "ЭФТРИ ПВХ", 40),
    ("лакокрас", "Лакокраска", 60),
    ("мдф плит", "МДФ плиты", 60),
    ("мдф", "МДФ", 30),
    ("хдф", "ХДФ", 40),
    ("лдсп", "ЛДСП", 20),
    ("столешн", "Столешницы", 50),
    ("петл", "Петли", 50),
    ("ящик", "Ящики", 50),
    ("стекл", "Стекло", 50),
    ("фасад", "Фасады", 30),
    ("алюмин", "Алюминиевый фасад", 30),
    ("комплектующ", "Комплектующие", 40),
    ("ручк", "Ручки", 50),
    ("профил", "Профили", 40),
    ("плит", "МДФ плиты", 20),
    ("фурнитур", "Фурнитура", 30),
)

# Совместимость: старые импорты/скрипты могли читать CATEGORY_MARKERS.
CATEGORY_MARKERS = {marker: category for marker, category, _ in CATEGORY_RULES}

DEFAULT_PRICE_UNIT = "шт"

# Допустимые единицы прайса (enum, а не угадывание по подстроке).
UNIT_ALIASES = {
    "шт": "шт",
    "штук": "шт",
    "шт.": "шт",
    "м": "м",
    "м.": "м",
    "м.п": "м",
    "м.п.": "м",
    "пог.м": "м",
    "пог.м.": "м",
    "м2": "м²",
    "м²": "м²",
    "кв.м": "м²",
    "кв.м.": "м²",
    "лист": "лист",
    "лст": "лист",
    "кг": "кг",
    "л": "л",
    "комплект": "комплект",
    "компл": "комплект",
    "компл.": "комплект",
}

# Категория → единица по умолчанию (используется, если ячейка/заголовок пусты).
CATEGORY_UNITS = {
    "Кромка": "м",
    "ЛДСП": "лист",
    "МДФ": "лист",
    "МДФ плиты": "лист",
    "ХДФ": "лист",
    "Столешницы": "м",
    "Фасады": "м²",
    "ЭФТРИ ПВХ": "м²",
    "Лакокраска": "м²",
    "Стекло": "м²",
    "Алюминиевый фасад": "м²",
}

# Толщина кромки/плиты: «0,8*19», «2 мм», «0.4».
_THICKNESS_CROSS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:мм)?\s*[*×xх]\s*\d+")
_THICKNESS_MM_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*мм\b")
_THICKNESS_DECIMAL_RE = re.compile(r"(?<![\d.,])(\d[.,]\d)(?![\d.,])")
_CURRENCY_RE = re.compile(r"(?i)(₽|руб\.?|р\.|rub)")


@dataclass(frozen=True)
class ImportStats:
    """Результат импорта прайса."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    rejected: int = 0
    deactivated: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "rejected": self.rejected,
            "deactivated": self.deactivated,
        }


def normalize_text(value: Optional[Any]) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = text.casefold()
    text = re.sub(r"[\s\W]+", " ", text)
    return text.strip()


def parse_number(value: Any) -> Optional[float]:
    """Число из ячейки цены.

    Допускает «1 250,50», «1250.5 ₽», но не вытаскивает цифры из текста:
    «LAMARTY 0,4*19» и «2800*2070» ценой не считаются.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    # Явные признаки не-цены: несколько чисел через «*», «x», дробь.
    if re.search(r"[*×xх/]", text.casefold()):
        return None
    text = _CURRENCY_RE.sub("", text)
    text = text.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    # Разрешаем знак, цифры и максимум одну точку.
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def detect_category_from_text(value: Optional[Any]) -> Optional[str]:
    """Категория по тексту: выигрывает самый специфичный (высокий приоритет).

    «КРОМКА К ЛДСП» содержит и «кромк», и «лдсп»; раньше побеждал ЛДСП,
    потому что проверялся первым в словаре.
    """
    text = normalize_text(value)
    if not text:
        return None
    best: Optional[tuple[int, str]] = None
    for marker, category, priority in CATEGORY_RULES:
        if marker in text and (best is None or priority > best[0]):
            best = (priority, category)
    return best[1] if best else None


def normalize_unit(value: Optional[Any]) -> Optional[str]:
    """Единица из ячейки/заголовка → enum или None."""
    text = normalize_text(value)
    if not text:
        return None
    text = text.replace(" ", "")
    if text in UNIT_ALIASES:
        return UNIT_ALIASES[text]
    # «руб/м2», «цена за м.п.»
    for alias in sorted(UNIT_ALIASES, key=len, reverse=True):
        if text.endswith(alias):
            return UNIT_ALIASES[alias]
    return None


def detect_price_unit(
    name: Optional[Any],
    category: Optional[Any],
    header: Optional[str],
    unit_cell: Optional[Any],
) -> str:
    """Единица измерения: явная ячейка → заголовок цены → категория.

    Больше не ищем букву «м» в произвольном тексте: это давало «м» для
    «Мойка» и «м²» для «ЛДСП».
    """
    explicit = normalize_unit(unit_cell)
    if explicit:
        return explicit

    header_unit = normalize_unit(header)
    if header_unit:
        return header_unit

    category_text = str(category or "")
    for marker, unit in CATEGORY_UNITS.items():
        if marker.casefold() in category_text.casefold():
            return unit

    name_unit = normalize_unit(name)
    if name_unit:
        return name_unit
    return DEFAULT_PRICE_UNIT


def detect_thickness_mm(name: Optional[Any], category: Optional[Any]) -> Optional[float]:
    """Толщина кромки из явного шаблона названия (P0-07).

    «Кромка 0.8*22» → 0.8, «Кромка 2*19» → 2.0, «Кромка ПВХ 0.4» → 0.4.
    Подстрока «2» больше не считается толщиной: «0.8*22» даёт 0.8.
    У ЛДСП «2800*2070» — формат листа, а не толщина, поэтому не угадываем.
    """
    text = str(name or "").strip()
    if not text:
        return None
    category_text = normalize_text(category)
    name_text = normalize_text(text)
    if "кромк" not in category_text and "кромк" not in name_text:
        return None

    for regex in (_THICKNESS_CROSS_RE, _THICKNESS_MM_RE):
        match = regex.search(text)
        if match:
            try:
                return float(match.group(1).replace(",", "."))
            except ValueError:
                continue

    # «Кромка ПВХ 0.4» — десятичная без «мм» и без «*».
    match = _THICKNESS_DECIMAL_RE.search(text)
    if match:
        return float(match.group(1).replace(",", "."))
    return None


def guess_header_row(sheet) -> tuple[int, dict[str, int]]:
    header_patterns = {
        "name": ["наимен", "назван", "товар", "позици"],
        "brand": ["бренд", "производ", "марка"],
        "category": ["категор", "раздел", "группа"],
        "price": ["цен", "р/м", "р/м2", "р/м²", "цена/м", "цена/м²"],
        "unit": ["ед", "ед.", "шт", "м2", "м²", "кв"],
    }

    for row_index in range(1, min(12, sheet.max_row) + 1):
        row = [normalize_text(cell.value) for cell in sheet[row_index]]
        if not any(row):
            continue

        mapping: dict[str, int] = {}
        for index, cell_text in enumerate(row):
            if not cell_text:
                continue
            for name, patterns in header_patterns.items():
                if any(pattern in cell_text for pattern in patterns):
                    if name not in mapping:
                        mapping[name] = index
        if "name" in mapping and "price" in mapping:
            return row_index, mapping

    # Fallback positions when headers are not obvious
    return 1, {"name": 0, "brand": 1, "category": 2, "price": 3, "unit": 4}


def is_section_header(row: list[Any]) -> bool:
    if not row or row[0] is None or not str(row[0]).strip():
        return False

    # Строка только с названием — обычно заголовок секции, НО товар с пустой
    # ценой выглядит так же. Названия товаров почти всегда содержат цифры
    # («LAMARTY 0,4*19»), поэтому такие строки считаем товаром (P0-01).
    if any(ch.isdigit() for ch in str(row[0])):
        return False

    rest = row[1:]
    return all(
        v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (int, float)) and v == 0)
        for v in rest
    )


def _item_key(name: str, category: str, brand: Optional[str]):
    """Идентичность позиции для поиска/деактивации (как в запросе импорта)."""
    return (name, category, brand or "")


async def import_price_from_xlsx(
    path: str,
    session: AsyncSession,
    changed_by: Optional[str] = None,
) -> Dict[str, int]:
    """Импорт прайса из XLSX в базу данных.

    Возвращает {"created", "updated", "skipped", "rejected", "deactivated"}.
    Позиции, которых нет в файле, деактивируются (is_active=0), а не удаляются.
    """
    workbook = load_workbook(Path(path), data_only=True)
    created = updated = skipped = rejected = deactivated = 0
    seen_keys: set = set()
    # Категории, реально присутствующие в файле: деактивируем пропавшие
    # позиции только внутри них. Иначе загрузка одного раздела прайса
    # выключила бы все остальные категории.
    imported_categories: set[str] = set()

    for sheet in workbook.worksheets:
        current_category = detect_category_from_text(sheet.title) or None
        header_row_index, header_mapping = guess_header_row(sheet)

        for row_index in range(header_row_index + 1, sheet.max_row + 1):
            row = [sheet.cell(row=row_index, column=col_index).value for col_index in range(1, max(sheet.max_column, 6) + 1)]
            if not any(row):
                continue

            if is_section_header(row):
                section_text = row[0]
                category_hint = detect_category_from_text(section_text)
                if category_hint:
                    current_category = category_hint
                continue

            name = row[header_mapping.get("name", 0)] if header_mapping.get("name") is not None else row[0]
            if name is None or not str(name).strip():
                skipped += 1
                continue

            name = str(name).strip()
            category_cell = row[header_mapping.get("category")] if header_mapping.get("category") is not None else None
            category_text = str(category_cell).strip() if category_cell is not None else ""
            category_hint = detect_category_from_text(category_text) or detect_category_from_text(name)
            if category_hint:
                category = category_hint
                subcategory = None
            else:
                category = current_category or category_text or "Прочее"
                subcategory = category_text if category_text and category_text != category else None

            brand_cell = row[header_mapping.get("brand")] if header_mapping.get("brand") is not None else None
            brand = str(brand_cell).strip() if brand_cell is not None and str(brand_cell).strip() else None
            if not brand:
                brand_guess = str(name).split(maxsplit=1)[0]
                if brand_guess.upper() in {"EGGER", "EXTRAVERT", "LAMARTY", "BOYARD", "BLUM", "HETTICH"}:
                    brand = brand_guess

            # P0-01: цена — только из своей ячейки, без перебора строки.
            price_cell = row[header_mapping.get("price")] if header_mapping.get("price") is not None else None
            price = parse_number(price_cell)
            if price is not None and price < 0:
                rejected += 1
                continue

            unit_cell = row[header_mapping.get("unit")] if header_mapping.get("unit") is not None else None
            price_header = (
                sheet.cell(
                    row=header_row_index,
                    column=header_mapping["price"] + 1,
                ).value
                if header_mapping.get("price") is not None
                else None
            )
            price_unit = detect_price_unit(name, category, price_header, unit_cell)
            thickness_mm = detect_thickness_mm(name, category)

            requires_manual_price = 1 if price is None or price == 0 else 0
            unit_price = None if requires_manual_price else float(price)

            # P0-07: кромка без явной толщины требует ручной проверки.
            if category == "Кромка" and thickness_mm is None and unit_price is not None:
                requires_manual_price = 1
                unit_price = None

            key = _item_key(name, category, brand)
            seen_keys.add(key)
            imported_categories.add(category)

            query = select(PriceItem).where(
                PriceItem.name == name,
                PriceItem.category == category,
                PriceItem.brand == brand,
            )
            result = await session.execute(query)
            price_item = result.scalars().first()

            if price_item is None:
                price_item = PriceItem(
                    name=name,
                    category=category,
                    subcategory=subcategory,
                    brand=brand,
                    unit_price=unit_price,
                    price_unit=price_unit,
                    thickness_mm=thickness_mm,
                    requires_manual_price=requires_manual_price,
                    is_active=1,
                )
                session.add(price_item)
                created += 1
                continue

            # P1-16: фиксируем старую цену ДО присваивания.
            old_price = price_item.unit_price
            old_price_value = float(old_price or 0)
            new_price_value = float(unit_price) if unit_price is not None else 0.0

            has_changed = False
            if price_item.unit_price != unit_price:
                price_item.unit_price = unit_price
                has_changed = True
            if price_item.price_unit != price_unit:
                price_item.price_unit = price_unit
                has_changed = True
            if price_item.thickness_mm != thickness_mm:
                price_item.thickness_mm = thickness_mm
                has_changed = True
            if price_item.category != category:
                price_item.category = category
                has_changed = True
            if price_item.subcategory != subcategory:
                price_item.subcategory = subcategory
                has_changed = True
            if price_item.brand != brand:
                price_item.brand = brand
                has_changed = True
            if price_item.requires_manual_price != requires_manual_price:
                price_item.requires_manual_price = requires_manual_price
                has_changed = True
            if not price_item.is_active:
                price_item.is_active = 1
                has_changed = True

            if old_price != unit_price:
                history = PriceHistory(
                    price_item=price_item,
                    old_price=old_price_value,
                    new_price=new_price_value,
                    changed_by=changed_by or "import",
                    change_reason="Импорт XLSX",
                )
                session.add(history)
                has_changed = True

            if has_changed:
                updated += 1
            else:
                skipped += 1

    # Деактивация позиций, которых нет в новом файле (не удаление!).
    result = await session.execute(select(PriceItem).where(PriceItem.is_active == 1))
    for item in result.scalars().all():
        if item.category not in imported_categories:
            continue
        if _item_key(item.name, item.category, item.brand) not in seen_keys:
            item.is_active = 0
            deactivated += 1

    await session.commit()
    return ImportStats(
        created=created,
        updated=updated,
        skipped=skipped,
        rejected=rejected,
        deactivated=deactivated,
    ).as_dict()
