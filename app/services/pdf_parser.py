"""
Сервис для парсинга PDF-альбомов чертежей и спецификаций через GLM-OCR.

GLM-OCR (Z.ai) — специализированная OCR-модель (0.9B параметров):
- OmniDocBench: 94.62 балла (мировой рекорд)
- Понимает: текст, таблицы (HTML), формулы, рукописный текст, печати
- Языки: русский, английский, китайский и др.
- Форматы: PDF (до 100 страниц, ≤50MB), JPG, PNG (≤10MB)
- Цена: $0.03/M токенов

Использование:
    from app.services.pdf_parser import GLMOCRParser

    parser = GLMOCRParser()
    result = await parser.parse_pdf("Альбом_чертежей.pdf")
    # result.rooms — список помещений с размерами и материалами
    # result.tables — распознанные таблицы
    # result.materials — все найденные материалы
"""

import re
import json
import logging
import base64
from typing import Dict, List, Optional, Any
from pathlib import Path
from dataclasses import dataclass, field

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ================================================================
# ДАТАКЛАССЫ
# ================================================================

@dataclass
class ParsedRoom:
    """Распознанное помещение из PDF-альбома."""
    name: str = ""                          # "Кухня", "Шкаф в прихожей"
    page: int = 0
    description: str = ""                   # текстовое описание
    dimensions: List[str] = field(default_factory=list)   # ["600×560×840", "3000×600"]
    materials: List[str] = field(default_factory=list)    # ["EGGER H1379", "МДФ"]
    notes: List[str] = field(default_factory=list)        # примечания
    raw_text: str = ""


@dataclass
class ParsedTable:
    """Распознанная таблица."""
    page: int = 0
    caption: str = ""
    headers: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)


@dataclass
class ParsedSpec:
    """Одна спецификация (модуль мебели с размерами)."""
    name: str = ""
    width_mm: Optional[int] = None
    depth_mm: Optional[int] = None
    height_mm: Optional[int] = None
    material: Optional[str] = None
    quantity: int = 1
    room: str = ""
    raw_line: str = ""


@dataclass
class PDFParseResult:
    """Результат парсинга PDF через GLM-OCR."""
    success: bool = False
    rooms: List[ParsedRoom] = field(default_factory=list)
    specifications: List[ParsedSpec] = field(default_factory=list)
    tables: List[ParsedTable] = field(default_factory=list)
    materials: List[str] = field(default_factory=list)
    dimensions_found: List[str] = field(default_factory=list)
    pages_processed: int = 0
    error: Optional[str] = None


# ================================================================
# GLM-OCR PARSER
# ================================================================

class GLMOCRParser:
    """
    Парсер PDF через GLM-OCR API (Z.ai).

    Эндпоинт: POST https://api.z.ai/api/paas/v4/layout_parsing
    """

    API_URL = "https://api.z.ai/api/paas/v4/layout_parsing"

    # Ключевые слова материалов для поиска
    MATERIAL_KEYWORDS = [
        "EGGER", "LAMARTY", "EXTRAVERT", "EMDIWAY", "ТОМЛЕСДРЕВ",
        "ЛДСП", "МДФ", "ХДФ", "ДСП", "шпон", "акрил", "пластик",
        "BLUM", "BOYARD", "HETTICH", "FIRMAX", "TANDEMBOX", "LEGRABOX",
        "GOLA", "кварц", "камень", "стекло", "зеркало",
    ]

    def __init__(self):
        self.api_key = settings.zai_api_key or settings.openrouter_api_key
        self.client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(180.0))
        return self.client

    async def close(self):
        if self.client:
            await self.client.aclose()
            self.client = None

    # -----------------------------------------------------------
    # ПУБЛИЧНЫЕ МЕТОДЫ
    # -----------------------------------------------------------

    async def parse_pdf(self, pdf_path: str | Path) -> PDFParseResult:
        """Распарсить PDF-альбом чертежей (до 100 страниц, ≤50MB)."""
        path = Path(pdf_path)
        if not path.exists():
            return PDFParseResult(success=False, error=f"Файл не найден: {path}")
        if path.stat().st_size > 50 * 1024 * 1024:
            return PDFParseResult(success=False, error="PDF > 50MB")

        logger.info(f"GLM-OCR: parsing {path.name} ({path.stat().st_size / 1024 / 1024:.1f}MB)...")

        try:
            client = await self._get_client()
            pdf_b64 = base64.b64encode(path.read_bytes()).decode()
            data_url = f"data:application/pdf;base64,{pdf_b64}"

            response = await client.post(
                self.API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": "glm-ocr", "file": data_url},
            )
            response.raise_for_status()
            return self._parse_response(response.json(), path.name)

        except httpx.HTTPStatusError as e:
            logger.error(f"GLM-OCR HTTP {e.response.status_code}")
            return PDFParseResult(success=False, error=f"HTTP {e.response.status_code}")
        except Exception as e:
            logger.error(f"GLM-OCR error: {e}")
            return PDFParseResult(success=False, error=str(e))

    async def parse_image(self, image_path: str | Path) -> PDFParseResult:
        """Распарсить изображение (JPG/PNG, ≤10MB)."""
        path = Path(image_path)
        if not path.exists():
            return PDFParseResult(success=False, error=f"Файл не найден: {path}")
        if path.stat().st_size > 10 * 1024 * 1024:
            return PDFParseResult(success=False, error="Изображение > 10MB")

        try:
            client = await self._get_client()
            img_b64 = base64.b64encode(path.read_bytes()).decode()
            ext = path.suffix.lower().lstrip(".")
            data_url = f"data:image/{ext};base64,{img_b64}"

            response = await client.post(
                self.API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": "glm-ocr", "file": data_url},
            )
            response.raise_for_status()
            return self._parse_response(response.json(), path.name)

        except Exception as e:
            logger.error(f"GLM-OCR image error: {e}")
            return PDFParseResult(success=False, error=str(e))

    # -----------------------------------------------------------
    # ПАРСИНГ ОТВЕТА
    # -----------------------------------------------------------

    def _parse_response(self, data: Dict[str, Any], source_name: str) -> PDFParseResult:
        """Главный парсер ответа GLM-OCR."""
        result = PDFParseResult()

        # Основной контент — md_results (Markdown с HTML-таблицами и картинками)
        md_content = data.get("md_results", "")
        if not md_content:
            result.error = "md_results отсутствует в ответе GLM-OCR"
            return result

        # Метаданные
        data_info = data.get("data_info", {})
        result.pages_processed = data_info.get("num_pages", 1)

        # 1. Парсим HTML-таблицы
        result.tables = self._parse_html_tables(md_content)

        # 2. Разбиваем на секции (помещения) по заголовкам ##
        result.rooms = self._parse_room_sections(md_content)

        # 3. Извлекаем спецификации (модули с размерами) из текста
        result.specifications = self._parse_specifications(md_content, result.rooms)

        # 4. Извлекаем все размеры
        result.dimensions_found = self._extract_all_dimensions(md_content)

        # 5. Извлекаем все материалы
        result.materials = self._extract_all_materials(md_content)

        result.success = True
        logger.info(
            f"GLM-OCR done: {result.pages_processed}p, "
            f"{len(result.rooms)} rooms, {len(result.specifications)} specs, "
            f"{len(result.tables)} tables, {len(result.materials)} materials"
        )
        return result

    # -----------------------------------------------------------
    # ПАРСИНГ СЕКЦИЙ (ПОМЕЩЕНИЙ)
    # -----------------------------------------------------------

    def _parse_room_sections(self, md: str) -> List[ParsedRoom]:
        """
        Разбить Markdown на секции по заголовкам ##.
        Каждый ## — это новое помещение.

        Фильтрует технические секции: Приемание, Примечание, Условные обозначения.
        """
        rooms = []

        # Заголовки, которые НЕ являются помещениями
        SKIP_HEADINGS = {
            "приемание", "приемание:", "примечание", "примечание:",
            "описание", "описание:", "условные обозначения", "условные обозначения:",
        }

        # Разбиваем по ## Заголовок
        sections = re.split(r'\n##\s+', md)
        if not sections:
            return rooms

        # Первый блок — всё до первого ## (обычно пустой или вводный)
        first_block = sections[0].strip()

        for i, section in enumerate(sections[1:], 1):
            lines = section.split('\n')
            name = lines[0].strip() if lines else f"Секция {i}"

            # Чистим имя
            name = re.sub(r'^#+\s*', '', name).strip()

            # Пропускаем технические секции
            if name.lower().rstrip(':') in SKIP_HEADINGS:
                continue

            # Собираем текст секции
            text = '\n'.join(lines[1:]) if len(lines) > 1 else ""

            room = ParsedRoom(
                name=name,
                page=i,  # примерно
                description=text[:500] if text else "",
                dimensions=self._extract_dimensions_from_text(text),
                materials=self._extract_materials_from_text(text),
                notes=self._extract_notes(text),
                raw_text=text,
            )
            rooms.append(room)

        return rooms

    # -----------------------------------------------------------
    # ПАРСИНГ HTML-ТАБЛИЦ
    # -----------------------------------------------------------

    def _parse_html_tables(self, md: str) -> List[ParsedTable]:
        """
        Извлечь HTML-таблицы из Markdown.
        GLM-OCR возвращает таблицы в формате <table border="1">...</table>.
        """
        tables = []

        # Находим все HTML-таблицы
        table_pattern = re.compile(r'<table[^>]*>(.*?)</table>', re.DOTALL)
        for match in table_pattern.finditer(md):
            html = match.group(1)

            # Проверяем, что перед таблицей (в пределах 200 символов) нет заголовка
            before = md[max(0, match.start() - 200):match.start()]
            caption = ""
            caption_match = re.search(r'([^\n]+)$', before)
            if caption_match:
                caption = caption_match.group(1).strip()[:100]

            # Парсим строки
            rows = []
            headers = []

            tr_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL)
            for tr_match in tr_pattern.finditer(html):
                cells = []
                td_pattern = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.DOTALL)
                for td_match in td_pattern.finditer(tr_match.group(1)):
                    cell_text = re.sub(r'<[^>]+>', '', td_match.group(1)).strip()
                    cells.append(cell_text)

                if cells:
                    if not headers and not any(rows):
                        headers = cells
                    else:
                        rows.append(cells)

            # Фильтруем пустые таблицы
            if headers or rows:
                tables.append(ParsedTable(
                    caption=caption,
                    headers=headers if headers else [],
                    rows=rows,
                ))

        return tables

    # -----------------------------------------------------------
    # ПАРСИНГ СПЕЦИФИКАЦИЙ
    # -----------------------------------------------------------

    def _parse_specifications(
        self, md: str, rooms: List[ParsedRoom]
    ) -> List[ParsedSpec]:
        """Извлечь спецификации модулей с размерами."""
        specs = []
        clean_text = re.sub(r'<[^>]+>', '', md)  # убираем HTML

        # Паттерны размеров:
        # - 600×560×840 (Ш×Г×В)
        # - 600x560x840
        # - 600*560*840
        # - размеры 600 560 840
        size_patterns = [
            re.compile(r'(\d{2,4})\s*[×xX\*хХ]\s*(\d{2,4})\s*[×xX\*хХ]\s*(\d{2,4})'),
            re.compile(r'(\d{2,4})\s+[×xX\*хХ]\s+(\d{2,4})'),  # Ш×Г
        ]

        # Ищем по строкам
        for line in clean_text.split('\n'):
            line = line.strip()
            if not line or len(line) < 3:
                continue

            for pattern in size_patterns:
                matches = pattern.findall(line)
                for match in matches:
                    try:
                        if len(match) == 3:
                            w, d, h = int(match[0]), int(match[1]), int(match[2])
                            # Игнорируем нереалистичные размеры
                            if w < 100 or w > 4000 or d < 100 or d > 1500 or h < 100 or h > 3000:
                                continue
                        elif len(match) == 2:
                            w, d = int(match[0]), int(match[1])
                            h = None
                        else:
                            continue
                    except ValueError:
                        continue

                    spec = ParsedSpec(
                        name=self._guess_item_name(line),
                        width_mm=w if len(match) >= 2 else None,
                        depth_mm=d if len(match) >= 2 else None,
                        height_mm=h if len(match) >= 3 and h else None,
                        material=self._find_material(line),
                        room=self._find_room_for_line(line, rooms),
                        raw_line=line,
                    )
                    specs.append(spec)
                    break  # один размер на строку

        # Дедупликация
        seen = set()
        unique = []
        for s in specs:
            key = f"{s.width_mm}x{s.depth_mm}x{s.height_mm}_{s.material}"
            if key not in seen:
                seen.add(key)
                unique.append(s)

        return unique

    def _guess_item_name(self, line: str) -> str:
        """Угадать название позиции по тексту строки."""
        line_lower = line.lower()
        keywords = [
            ("шкаф", "Шкаф"), ("тумб", "Тумба"), ("пенал", "Пенал"),
            ("база", "База"), ("витрин", "Витрина"), ("столешн", "Столешница"),
            ("зеркал", "Зеркало"), ("полк", "Полка"), ("ящик", "Ящик"),
            ("фасад", "Фасад"), ("цокол", "Цоколь"), ("карниз", "Карниз"),
            ("сушк", "Сушка"), ("бутылоч", "Бутылочница"),
        ]
        for kw, name in keywords:
            if kw in line_lower:
                return name
        return "Модуль"

    def _find_room_for_line(self, line: str, rooms: List[ParsedRoom]) -> str:
        """Определить, к какому помещению относится строка (эвристика)."""
        # Ищем упоминание комнаты в самой строке
        room_keywords = {
            "кухн": "Кухня", "гостин": "Гостиная", "спальн": "Спальня",
            "прихож": "Прихожая", "ванн": "Ванная", "сануз": "Санузел",
            "гардероб": "Гардеробная", "постироч": "Постирочная",
            "кабинет": "Кабинет", "балкон": "Балкон", "лоджи": "Лоджия",
            "столов": "Столовая",
        }
        line_lower = line.lower()
        for kw, room_name in room_keywords.items():
            if kw in line_lower:
                return room_name
        return ""

    # -----------------------------------------------------------
    # ИЗВЛЕЧЕНИЕ РАЗМЕРОВ
    # -----------------------------------------------------------

    def _extract_all_dimensions(self, md: str) -> List[str]:
        """Извлечь все размеры из Markdown."""
        clean = re.sub(r'<[^>]+>', '', md)
        patterns = [
            r'\d{2,4}\s*[×xX\*]\s*\d{2,4}(\s*[×xX\*]\s*\d{2,4})?',
            r'\d{2,4}\s*мм',
        ]
        found = []
        for pat in patterns:
            matches = re.findall(pat, clean, re.IGNORECASE)
            found.extend(m.strip() if isinstance(m, str) else m for m in matches)
        # Уникальные, сохраняя порядок
        return list(dict.fromkeys(found))

    def _extract_dimensions_from_text(self, text: str) -> List[str]:
        """Извлечь размеры из текстового блока."""
        return self._extract_all_dimensions(text)

    # -----------------------------------------------------------
    # ИЗВЛЕЧЕНИЕ МАТЕРИАЛОВ
    # -----------------------------------------------------------

    def _extract_all_materials(self, md: str) -> List[str]:
        """Извлечь все упоминания материалов."""
        return self._extract_materials_from_text(md)

    def _extract_materials_from_text(self, text: str) -> List[str]:
        """Извлечь материалы из текста."""
        found = set()
        text_upper = text.upper()
        for kw in self.MATERIAL_KEYWORDS:
            if kw.upper() in text_upper:
                # Ищем полное название (ключевое слово + следующее слово)
                idx = text_upper.find(kw.upper())
                if idx >= 0:
                    end = text.find('\n', idx) if '\n' in text[idx:] else len(text)
                    snippet = text[idx:min(idx + 60, end)].strip()
                    found.add(snippet if len(snippet) < 40 else kw)
        return sorted(found)

    def _find_material(self, line: str) -> Optional[str]:
        """Найти материал в одной строке."""
        materials = self._extract_materials_from_text(line)
        return materials[0] if materials else None

    # -----------------------------------------------------------
    # ПРИМЕЧАНИЯ
    # -----------------------------------------------------------

    def _extract_notes(self, text: str) -> List[str]:
        """Извлечь примечания (нумерованные списки, требования)."""
        notes = []
        # Нумерованные пункты: "1. ", "2. ", ...
        for match in re.finditer(r'^\s*(\d+)\\.?\\s+(.+)$', text, re.MULTILINE):
            notes.append(match.group(2).strip())
        # "Примечание:" или "Приемание:"
        note_sections = re.split(r'(?:Примечание|Приемание|ВАЖНО|ВНИМАНИЕ)[\s:]*', text, flags=re.IGNORECASE)
        if len(note_sections) > 1:
            for ns in note_sections[1:]:
                first_line = ns.strip().split('\n')[0]
                if first_line and len(first_line) > 5:
                    notes.append(first_line)
        return notes


# ================================================================
# ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР
# ================================================================

_ocr_parser: Optional[GLMOCRParser] = None


def get_ocr_parser() -> GLMOCRParser:
    global _ocr_parser
    if _ocr_parser is None:
        _ocr_parser = GLMOCRParser()
    return _ocr_parser
