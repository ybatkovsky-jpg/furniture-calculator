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
from typing import Any

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
    ("лоток", "Комплектующие", 60),
    ("ящик", "Ящики", 50),
    ("направляющ", "Направляющие", 50),
    ("подъёмник", "Подъёмники", 50),
    ("подъемник", "Подъёмники", 50),
    ("бутылочниц", "Бутылочницы", 50),
    ("сушк", "Сушки", 50),
    ("подсвет", "Подсветка", 50),
    ("gola", "GOLA", 50),
    ("гола", "GOLA", 50),
    ("цокол", "Крепёж", 50),
    ("конфирмат", "Крепёж", 60),
    ("опора", "Крепёж", 50),
    ("подвес", "Крепёж", 50),
    ("подрядчик", "Подрядчики", 50),
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
    "GOLA": "шт",
    "Крепёж": "шт",
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
_EDGE_NAME_RE = re.compile(r"(?<![\d.,])(\d(?:[.,]\d)?)\s*[*×xх]\s*(19|22|23|35|44)\b")
_DIMENSIONS_RE = re.compile(r"(\d{3,4})\s*[*×xх]\s*(\d{2,4})")
_COIL_RE = re.compile(r"бухт\w*[^\d]{0,5}(\d+(?:[.,]\d+)?)\s*м", re.IGNORECASE)


def looks_like_edge(name: Any | None) -> bool:
    """«МДФ ETERNO 1*22», «EGGER 0,8*19» — это кромка, а не плита."""
    return bool(_EDGE_NAME_RE.search(str(name or "")))


def detect_brand(name: str, category: str) -> str | None:
    """Бренд позиции для выбора в боте (категории важнее первого слова).

    UI выбирает петли по бренду BOYARD/BLUM/HETTICH и ящики по
    «BLUM TANDEMBOX»/«BLUM LEGRABOX»/«BOYARD СТАРТ»/HETTICH. Импорт без
    бренда ломал эти запросы.
    """
    text = (name or "").upper()
    known = {
        "EGGER", "EXTRAVERT", "LAMARTY", "ТОМЛЕСДРЕВ", "TOMLESDREV",
        "EVOGLOSS", "EVOSOFT", "AGT", "ETERNO", "SLOTEX", "FIRMAX",
        "BOYARD", "BLUM", "HETTICH",
    }
    first = (name or "").split(maxsplit=1)[0].upper() if name else ""
    if first in known:
        return first

    if category == "Петли":
        if "BOYARD" in text or "БОЯРД" in text:
            return "BOYARD"
        if "BLUM" in text or "БЛЮМ" in text:
            return "BLUM"
        if "HETTICH" in text or "ХЕТТИХ" in text:
            return "HETTICH"
        return None

    if category == "Ящики":
        if "TANDEMBOX" in text or "LEGRABOX" in text or "MOVENTO" in text:
            return "BLUM LEGRABOX" if "LEGRABOX" in text else "BLUM TANDEMBOX"
        if "BOYARD" in text or "СТАРТ" in text:
            return "BOYARD СТАРТ"
        if "HETTICH" in text:
            return "HETTICH"
        if "BLUM" in text:
            return "BLUM TANDEMBOX"
        return None

    for marker in ("BOYARD", "BLUM", "HETTICH", "EGGER", "SLOTEX", "AGT"):
        if marker in text:
            return marker
    return None


@dataclass(frozen=True)
class ImportStats:
    """Результат импорта прайса."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    rejected: int = 0
    deactivated: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "rejected": self.rejected,
            "deactivated": self.deactivated,
        }


def normalize_text(value: Any | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # ё → е и снятие диакритики: NFKD разлагает «ё» на «е» + U+0308,
    # а regex превращал combining mark в пробел («подъе мники»).
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"[\s\W]+", " ", text)
    return text.strip()


def parse_number(value: Any) -> float | None:
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


def detect_category_from_text(value: Any | None) -> str | None:
    """Категория по тексту: выигрывает самый специфичный (высокий приоритет).

    «КРОМКА К ЛДСП» содержит и «кромк», и «лдсп»; раньше побеждал ЛДСП,
    потому что проверялся первым в словаре.
    """
    text = normalize_text(value)
    if not text:
        return None
    best: tuple[int, str] | None = None
    for marker, category, priority in CATEGORY_RULES:
        if marker in text and (best is None or priority > best[0]):
            best = (priority, category)
    return best[1] if best else None


def normalize_unit(value: Any | None) -> str | None:
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
    name: Any | None,
    category: Any | None,
    header: str | None,
    unit_cell: Any | None,
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

    name_text = str(name or "")
    name_lower = name_text.casefold()
    # Профили GOLA и цоколь продаются штукой длиной 3–4 м, а не за метр.
    if ("gola" in name_lower or "гола" in name_lower) and "профил" in name_lower:
        return "шт"
    if "цокол" in name_lower:
        return "шт"
    if "м²" in name_text or "кв.м" in name_lower or "кв. м" in name_lower:
        return "м²"
    # «3 м», «5м» — но не «900мм» (это размер, а не единица).
    if re.search(r"\d+\s*м(?!м)(?!\w)", name_text, re.IGNORECASE):
        return "м"
    if re.search(r"\d+\s*м\.п", name_lower):
        return "м"
    if "шт" in name_lower and not re.search(r"\d+\s*мм", name_lower):
        return "шт"
    return DEFAULT_PRICE_UNIT


def detect_thickness_mm(name: Any | None, category: Any | None) -> float | None:
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
                value = float(match.group(1).replace(",", "."))
            except ValueError:
                continue
            # Кромка толщиной 42 мм — это не кромка, а «42*3050» (габарит).
            if 0.2 <= value <= 3.0:
                return value
            continue

    # «Кромка ПВХ 0.4» — десятичная без «мм» и без «*».
    match = _THICKNESS_DECIMAL_RE.search(text)
    if match:
        return float(match.group(1).replace(",", "."))
    return None


def guess_header_row(sheet) -> tuple[int, dict[str, int]]:
    header_patterns = {
        "name": ["наимен", "назван", "товар", "позици"],
        # «Поставщик» — это не бренд ЛДСП: бренд берём из названия (EGGER и т.п.).
        "brand": ["бренд", "производ", "марка"],
        "color": ["цвет"],
        "category": ["категор", "раздел", "группа"],
        # «С/С» — себестоимость, то есть цена позиции в файлах расчёта.
        "price": ["с/с", "с с", "себестоим", "цен", "р/м", "р м", "р/м2", "р м2", "цена/м", "цена/м²"],
        "unit": ["ед", "ед.", "шт", "м2", "м²", "кв"],
    }

    for row_index, values in enumerate(
        sheet.iter_rows(min_row=1, max_row=12, values_only=True), 1
    ):
        row = [normalize_text(value) for value in values]
        if not any(row):
            continue

        mapping: dict[str, int] = {}
        for index, cell_text in enumerate(row):
            if not cell_text:
                continue
            for name, patterns in header_patterns.items():
                if any(pattern in cell_text for pattern in patterns) and name not in mapping:
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


def _item_key(name: str, category: str, brand: str | None):
    """Идентичность позиции для поиска/деактивации (как в запросе импорта)."""
    return (name, category, brand or "")


def _sheet_bounds(sheet) -> tuple[int, int]:
    """Границы листа для read_only-режима (max_row там None)."""
    if sheet.max_row and sheet.max_column:
        return int(sheet.max_row), int(sheet.max_column)
    try:
        from openpyxl.utils import range_boundaries

        _min_col, _min_row, max_col, max_row = range_boundaries(
            sheet.calculate_dimension(force=True)
        )
        return int(max_row), int(max_col)
    except Exception:  # noqa: BLE001
        return 0, 0


async def import_price_from_xlsx(
    path: str,
    session: AsyncSession,
    changed_by: str | None = None,
) -> dict[str, int]:
    """Импорт прайса из XLSX в базу данных.

    Возвращает {"created", "updated", "skipped", "rejected", "deactivated"}.
    Позиции, которых нет в файле, деактивируются (is_active=0), а не удаляются.
    """
    # read_only — защита от больших/zip-bomb файлов (P2-10).
    workbook = load_workbook(Path(path), data_only=True, read_only=True)
    created = updated = skipped = rejected = deactivated = 0
    seen_keys: set = set()
    # Категории, реально присутствующие в файле: деактивируем пропавшие
    # позиции только внутри них. Иначе загрузка одного раздела прайса
    # выключила бы все остальные категории.
    imported_categories: set[str] = set()

    for sheet in workbook.worksheets:
        current_category = detect_category_from_text(sheet.title) or None
        current_brand = None  # бренд из заголовка секции («ЯЩИКИ HETTICH»)
        header_row_index, header_mapping = guess_header_row(sheet)
        # read_only: читаем лист один раз построчно, а не random-access cell().
        all_rows = list(sheet.iter_rows(values_only=True))
        max_row = len(all_rows)
        max_col = max((len(r) for r in all_rows), default=0)

        for row_index in range(header_row_index + 1, max_row + 1):
            raw = list(all_rows[row_index - 1])
            row = raw + [None] * (max(max_col, 6) - len(raw))
            if not any(row):
                continue

            if is_section_header(row):
                section_text = row[0]
                category_hint = detect_category_from_text(section_text)
                if category_hint:
                    current_category = category_hint
                section_brand = detect_brand(str(section_text), current_category or "")
                if section_brand:
                    current_brand = section_brand
                continue

            name = row[header_mapping.get("name", 0)] if header_mapping.get("name") is not None else row[0]
            if name is None or not str(name).strip():
                skipped += 1
                continue

            name = str(name).strip()
            # Цвет/вариант — часть идентичности позиции: «EGGER ЛДСП» бывает
            # однотон (6000) и текстура (6650), иначе строки перезаписывают
            # друг друга и остаётся одна цена.
            color_cell = (
                row[header_mapping["color"]]
                if header_mapping.get("color") is not None
                else None
            )
            color = str(color_cell).strip() if color_cell is not None else ""
            if color and color.casefold() not in name.casefold():
                name = f"{name} {color}"
            category_cell = row[header_mapping.get("category")] if header_mapping.get("category") is not None else None
            category_text = str(category_cell).strip() if category_cell is not None else ""
            # Приоритет: явная колонка категории → заголовок секции → имя.
            # Имя не должно перебивать секцию: «металл опора» в разделе
            # «ОТ ПОДРЯДЧИКОВ» — это подрядчик, а не крепёж.
            category_hint = detect_category_from_text(category_text)
            name_hint = detect_category_from_text(name)
            # Сильные маркеры в названии важнее заголовка секции:
            # «IVEGO ПВХ» не должен наследовать случайную «Кромку».
            strong_hint = name_hint in {
                "ЛДСП", "Кромка", "МДФ", "МДФ плиты", "ХДФ", "Фасады",
                "ЭФТРИ ПВХ", "Лакокраска", "Алюминиевый фасад", "Стекло",
                "Петли", "Ящики", "Ручки", "GOLA",
            }
            if category_hint:
                category = category_hint
                subcategory = None
            elif strong_hint:
                category = name_hint
                subcategory = None
            elif current_category:
                category = current_category
                subcategory = category_text if category_text and category_text != category else None
            elif name_hint:
                category = name_hint
                subcategory = None
            else:
                category = category_text or "Прочее"
                subcategory = None

            # «МДФ ETERNO 1*22» / «EGGER 0,8*19» — кромка, даже если строка
            # стоит до заголовка «КРОМКА К МДФ» и в названии есть «МДФ».
            if looks_like_edge(name):
                category = "Кромка"
                subcategory = None

            brand_cell = row[header_mapping.get("brand")] if header_mapping.get("brand") is not None else None
            brand = str(brand_cell).strip() if brand_cell is not None and str(brand_cell).strip() else None
            if not brand:
                brand = detect_brand(str(name), category)
                if not brand and category in (
                    "Петли", "Ящики", "Подъёмники", "Направляющие"
                ):
                    brand = current_brand

            # P0-01: цена — только из своей ячейки, без перебора строки.
            price_cell = row[header_mapping.get("price")] if header_mapping.get("price") is not None else None
            price = parse_number(price_cell)
            if price is not None and price < 0:
                rejected += 1
                continue

            unit_cell = row[header_mapping.get("unit")] if header_mapping.get("unit") is not None else None
            price_header = None
            if header_mapping.get("price") is not None and header_row_index - 1 < len(all_rows):
                header_row = all_rows[header_row_index - 1]
                price_index = header_mapping["price"]
                if price_index < len(header_row):
                    price_header = header_row[price_index]
            price_unit = detect_price_unit(name, category, price_header, unit_cell)
            thickness_mm = detect_thickness_mm(name, category)

            # Цены «за изделие» приводим к единице расчёта:
            # - столешница «4100*600» с ценой за лист → ₽/м по длине;
            # - LED «Бухта 5м» → ₽/м;
            # - профиль/цоколь длиной 3–4 м → ₽/шт (так их считает смета).
            if price:
                if category == "Столешницы" and price_unit == "м":
                    dims = _DIMENSIONS_RE.search(str(name))
                    if dims:
                        length_mm = max(int(dims.group(1)), int(dims.group(2)))
                        if length_mm > 0:
                            price = round(float(price) / (length_mm / 1000), 2)
                elif category == "Подсветка" and price_unit == "м":
                    coil = _COIL_RE.search(str(name))
                    if coil:
                        length_m = float(coil.group(1).replace(",", "."))
                        if length_m > 0:
                            price = round(float(price) / length_m, 2)
                elif category in ("GOLA", "Крепёж") and price_unit == "м":
                    price_unit = "шт"

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

    workbook.close()
    await session.commit()
    return ImportStats(
        created=created,
        updated=updated,
        skipped=skipped,
        rejected=rejected,
        deactivated=deactivated,
    ).as_dict()
