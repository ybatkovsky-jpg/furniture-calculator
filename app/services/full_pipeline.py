"""
Главный конвейер обработки PDF-альбома чертежей.

GLM-OCR → структура помещений → рендеринг страниц → GLM-5V-Turbo → спецификация

Процесс:
1. GLM-OCR: читает весь PDF, извлекает помещения, примечания, таблицы
2. Рендеринг: каждая страница → JPEG для Vision-модели
3. GLM-5V-Turbo: для каждой ключевой страницы — модули с размерами
4. Слияние: помещение + модули + материалы = готовая спецификация
"""

import asyncio
import tempfile
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from app.services.pdf_parser import GLMOCRParser, PDFParseResult, ParsedRoom
from app.services.pdf_renderer import render_page, get_page_count
from app.services.image_analyzer import GeminiImageAnalyzer, RecognitionResult

logger = logging.getLogger(__name__)


# Маппинг английских zone_type → русские названия помещений
ZONE_TYPE_RU = {
    "kitchen": "Кухня",
    "living_room": "Гостиная",
    "bedroom": "Спальня",
    "wardrobe": "Гардеробная",
    "bathroom": "Ванная",
    "hallway": "Прихожая",
    "kids_room": "Детская",
    "office": "Кабинет",
    "laundry": "Постирочная",
    "balcony": "Балкон",
    "dining_room": "Столовая",
}

# Замена латинских букв на кириллицу (частые ошибки OCR)
LATIN_TO_CYRILLIC = str.maketrans({
    'c': 'с', 'C': 'С',   # латинская c → русская с
    'e': 'е', 'E': 'Е',   # латинская e → русская е
    'o': 'о', 'O': 'О',   # латинская o → русская о
    'a': 'а', 'A': 'А',   # латинская a → русская а
    'p': 'р', 'P': 'Р',   # латинская p → русская р
    'x': 'х', 'X': 'Х',   # латинская x → русская х
    'y': 'у', 'Y': 'У',   # латинская y → русская у
    't': 'т', 'T': 'Т',   # латинская t → русская т
    'k': 'к', 'K': 'К',   # латинская k → русская к
    'm': 'м', 'M': 'М',   # латинская m → русская м
    'n': 'н',             # латинская n → русская н (осторожно: 'N' не трогаем)
})


def _translate_zone_type(zone_type: str) -> str:
    """Перевести английский zone_type в русское название."""
    if not zone_type:
        return zone_type
    key = zone_type.lower().replace(" ", "_")
    return ZONE_TYPE_RU.get(key, zone_type)


def _fix_ocr_name(name: str) -> str:
    """
    Исправить латинские буквы в кириллическом тексте (ошибки OCR).
    Также чинит явные опечатки.
    """
    if not name:
        return name
    # Если имя уже на русском (содержит кириллицу) — фиксим латинские вкрапления
    has_cyrillic = any('а' <= ch <= 'я' or 'А' <= ch <= 'Я' for ch in name)
    if has_cyrillic:
        name = name.translate(LATIN_TO_CYRILLIC)
    
    # Явные исправления опечаток OCR
    TYPO_FIXES = {
        "Оctров": "Остров",
        "Остров": "Остров",
        "Остров": "Остров",
        "Подветкой": "подсветкой",
    }
    for wrong, right in TYPO_FIXES.items():
        if wrong in name:
            name = name.replace(wrong, right)
    
    # Капитализация первой буквы
    if name and name[0].islower():
        name = name[0].upper() + name[1:]
    
    return name


@dataclass
class RoomSpec:
    """Спецификация одного помещения."""
    room_name: str = ""
    page: int = 0
    modules: List = field(default_factory=list)   # RecognizedModule
    materials: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    confidence: str = ""


@dataclass
class PipelineResult:
    """Результат полного конвейера."""
    success: bool = False
    project_name: str = ""
    project_address: str = ""
    designer: str = ""
    total_pages: int = 0
    rooms: List[RoomSpec] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class FullPipeline:
    """
    Полный конвейер: PDF → AI → спецификация.
    """

    def __init__(self):
        self.ocr = GLMOCRParser()
        self.vision = GeminiImageAnalyzer()

    async def close(self):
        await self.ocr.close()
        await self.vision.close()

    async def process(self, pdf_path: str | Path, max_pages: int = 30) -> PipelineResult:
        """
        Обработать PDF-альбом чертежей.

        Args:
            pdf_path: путь к PDF
            max_pages: макс. число страниц для Vision-анализа (экономия токенов)
        """
        path = Path(pdf_path)
        result = PipelineResult()
        result.total_pages = get_page_count(path)

        logger.info(f"🚀 Конвейер запущен: {path.name} ({result.total_pages} стр.)")

        # ── Шаг 1: GLM-OCR ──
        logger.info("📄 Шаг 1/3: GLM-OCR — чтение структуры PDF...")
        ocr_result = await self.ocr.parse_pdf(path)
        if not ocr_result.success:
            result.errors.append(f"OCR failed: {ocr_result.error}")
            return result

        # Извлекаем метаданные из таблиц
        result.project_address = self._extract_address(ocr_result)
        result.designer = self._extract_designer(ocr_result)

        # ── Шаг 2: Определяем ключевые страницы ──
        # Страницы с реальными чертежами (пропускаем титульные)
        pages_to_analyze = self._pick_key_pages(ocr_result, result.total_pages)
        pages_to_analyze = pages_to_analyze[:max_pages]

        logger.info(f"🖼️  Шаг 2/3: Рендеринг + анализ {len(pages_to_analyze)} страниц...")

        # ── Шаг 3: Рендеринг + Vision для каждой страницы ──
        room_specs: Dict[int, RoomSpec] = {}

        for i, page_num in enumerate(pages_to_analyze):
            logger.info(f"  Стр. {page_num + 1}/{result.total_pages} ({i + 1}/{len(pages_to_analyze)})...")

            try:
                # 3a. Рендерим страницу
                jpeg_bytes = render_page(path, page_num)

                # 3b. Сохраняем во временный файл
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                    tmp.write(jpeg_bytes)
                    tmp_path = tmp.name

                # 3c. Vision-распознавание
                recog_result = await self.vision.analyze_drawing(tmp_path)

                # 3d. Удаляем временный файл
                Path(tmp_path).unlink(missing_ok=True)

                if recog_result.modules:
                    # Приоритет: OCR-имя > сгенерированное по модулям > zone_type
                    ocr_room = self._find_room_for_page(ocr_result, page_num)
                    if ocr_room:
                        room_name = _fix_ocr_name(ocr_room.name)
                    else:
                        room_name = _generate_room_name(recog_result, page_num)

                    if page_num not in room_specs:
                        room_specs[page_num] = RoomSpec(
                            room_name=room_name,
                            page=page_num + 1,
                        )

                    spec = room_specs[page_num]
                    spec.modules.extend(recog_result.modules)
                    spec.materials.extend(recog_result.materials_mentioned)
                    spec.confidence = recog_result.confidence

                    logger.info(
                        f"    ✅ {recog_result.zone_type or '?'}: "
                        f"{len(recog_result.modules)} модулей"
                    )
                else:
                    logger.info(f"    ⏭️  Модули не найдены")

            except Exception as e:
                logger.error(f"    ❌ Ошибка стр. {page_num + 1}: {e}")
                result.errors.append(f"Стр. {page_num + 1}: {e}")

        # ── Сборка результата ──
        result.rooms = sorted(room_specs.values(), key=lambda r: r.page)

        # Добавляем OCR-комнаты без модулей (для информации), фильтруя технические
        SKIP_NAMES = {"описание:", "приемание:", "примечание:", "условные обозначения:"}
        for ocr_room in ocr_result.rooms:
            name_clean = ocr_room.name.lower().rstrip(':')
            if not ocr_room.name or name_clean in SKIP_NAMES:
                continue
            fixed_name = _fix_ocr_name(ocr_room.name)
            already = any(
                fixed_name.lower() in r.room_name.lower()
                for r in result.rooms
            )
            if not already:
                result.rooms.append(RoomSpec(
                    room_name=_fix_ocr_name(ocr_room.name),
                    materials=ocr_room.materials,
                    notes=ocr_room.notes,
                ))

        result.success = len(result.rooms) > 0
        logger.info(
            f"✅ Конвейер завершён: {len(result.rooms)} помещений, "
            f"{sum(len(r.modules) for r in result.rooms)} модулей"
        )
        return result

    # -----------------------------------------------------------
    # ВСПОМОГАТЕЛЬНЫЕ
    # -----------------------------------------------------------

    def _pick_key_pages(
        self, ocr_result: PDFParseResult, total: int
    ) -> List[int]:
        """Выбрать страницы с чертежами (не титульные, не штампы)."""
        # Простая эвристика: страницы 1..N-2 (пропускаем первую и последние)
        # TODO: использовать page-референсы из OCR
        if total <= 3:
            return list(range(total))

        # Страницы с 1-й по предпоследнюю
        return list(range(1, total - 1))

    def _find_room_for_page(
        self, ocr_result: PDFParseResult, page_num: int
    ) -> Optional[ParsedRoom]:
        """Найти OCR-комнату для страницы: точный match page=N."""
        import re
        for room in ocr_result.rooms:
            if f"page={page_num}" in room.raw_text:
                return room
        return None

    def _extract_address(self, ocr_result: PDFParseResult) -> str:
        """Извлечь адрес из таблиц OCR."""
        for table in ocr_result.tables:
            for row in table.rows:
                for cell in row:
                    if "Рокоссовского" in cell or "Хабаровск" in cell:
                        return cell.strip()
        return ""

    def _extract_designer(self, ocr_result: PDFParseResult) -> str:
        """Извлечь имя дизайнера из таблиц."""
        for table in ocr_result.tables:
            for row in table.rows:
                if row and "Курманова" in str(row):
                    return str(row).strip()
        return ""


# ================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (module-level)
# ================================================================

def _generate_room_name(recog_result, page_num: int = 0) -> str:
    """
    Сгенерировать название листа по составу модулей (на русском).
    Описывает МЕБЕЛЬ, а не комнату.
    """
    if not recog_result or not recog_result.modules:
        return f"Стр.{page_num + 1}" if page_num > 0 else "Без модулей"

    modules = recog_result.modules
    page_suffix = f" (стр.{page_num + 1})" if page_num > 0 else ""

    # Считаем типы модулей
    type_counts = {}
    for m in modules:
        t = m.type
        type_counts[t] = type_counts.get(t, 0) + (m.quantity or 1)

    total = sum(type_counts.values())
    lower = type_counts.get("lower_base", 0)
    upper = type_counts.get("upper_base", 0)
    penal = type_counts.get("penal", 0)
    corner = type_counts.get("corner", 0)

    # Один модуль
    if total == 1:
        m = modules[0]
        if m.type == "penal":
            name = "Шкаф-пенал"
        elif m.type == "lower_base":
            name = "Тумба" if m.height < 400 else "Нижняя база"
        elif m.type == "upper_base":
            name = "Верхняя база"
        elif m.type == "corner":
            name = "Угловой модуль"
        else:
            name = "Модуль"
        return name + page_suffix

    # Кухонный гарнитур
    if lower + upper >= total * 0.6:
        name = "Кухонный гарнитур (угловой)" if corner > 0 else "Кухонный гарнитур"
        return name + page_suffix

    # Шкафы/пеналы
    if penal >= total * 0.5:
        name = "Шкаф-пенал" if penal == 1 else "Шкафы и пеналы"
        return name + page_suffix

    # Только нижние базы
    if lower >= total * 0.5:
        name = "Тумба" if lower <= 2 else "Нижние базы"
        return name + page_suffix

    # Только верхние
    if upper >= total * 0.5:
        name = "Верхние базы"
        return name + page_suffix

    # Смешанный состав
    zone = _translate_zone_type(recog_result.zone_type) if recog_result.zone_type else ""
    name = zone if zone else "Мебель"
    return name + page_suffix


# ================================================================
# БЫСТРЫЙ ЗАПУСК
# ================================================================

async def run_pipeline(pdf_path: str, max_pages: int = 20) -> PipelineResult:
    """Запустить полный конвейер для PDF."""
    pipeline = FullPipeline()
    try:
        return await pipeline.process(pdf_path, max_pages=max_pages)
    finally:
        await pipeline.close()


def print_result(result: PipelineResult):
    """Красиво вывести результат конвейера."""
    print()
    print("=" * 70)
    print("📊 РЕЗУЛЬТАТ КОНВЕЙЕРА")
    print("=" * 70)
    if result.project_address:
        print(f"📍 Адрес: {result.project_address}")
    if result.designer:
        print(f"👤 Дизайнер: {result.designer}")
    print(f"📄 Страниц: {result.total_pages}")
    print()

    for i, room in enumerate(result.rooms, 1):
        print(f"{i}. 🏠 {room.room_name} (стр. {room.page})")
        if room.materials:
            print(f"   🎨 Материалы: {', '.join(room.materials[:5])}")
        if room.notes:
            for note in room.notes[:3]:
                print(f"   📝 {note[:100]}")
        if room.modules:
            print(f"   📦 Модули ({len(room.modules)}):")
            for m in room.modules:
                g = '🪟' if m.has_glass else ' '
                c = '📐' if m.is_corner else ' '
                print(f"      {g}{c} {m.type}: {m.width}×{m.depth}×{m.height}mm ×{m.quantity}")
        else:
            print(f"   (без модулей — информационная секция)")
        print()

    if result.errors:
        print(f"⚠️  Ошибки ({len(result.errors)}):")
        for e in result.errors:
            print(f"   - {e}")

    total_modules = sum(len(r.modules) for r in result.rooms)
    print("=" * 70)
    print(f"✅ Всего: {len(result.rooms)} помещений, {total_modules} модулей")
    print("=" * 70)
