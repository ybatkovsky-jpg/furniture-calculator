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
                    # Находим или создаём спецификацию комнаты
                    room_name = recog_result.zone_type or f"Страница {page_num + 1}"

                    # Пытаемся найти имя комнаты из OCR
                    ocr_room = self._find_room_for_page(ocr_result, page_num)
                    if ocr_room:
                        room_name = ocr_room.name

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
            already = any(
                ocr_room.name.lower() in r.room_name.lower()
                for r in result.rooms
            )
            if not already:
                result.rooms.append(RoomSpec(
                    room_name=ocr_room.name,
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
        """
        Выбрать страницы, которые с высокой вероятностью содержат чертежи.
        
        Использует данные OCR для пропуска титульных страниц, содержания,
        ведомостей, штампов и технических секций.
        """
        if total <= 3:
            return list(range(total))

        # Индикаторы НЕ-чертежей (титул, содержание, ведомость, штамп, примечания)
        SKIP_INDICATORS = [
            "содержание", "ведомость", "спецификация", "титул",
            "примечание", "приемание", "условные обозначения",
            "штамп", "печать", "общие данные", "общие указания",
        ]

        # Собираем номера страниц, которые точно НЕ чертежи,
        # на основе имён OCR-комнат
        skip_pages: set[int] = set()
        for room in ocr_result.rooms:
            name_lower = room.name.lower().rstrip(':')
            if any(skip in name_lower for skip in SKIP_INDICATORS):
                if room.page > 0:
                    skip_pages.add(room.page - 1)  # page в OCR = 1-based

        # Если OCR не дал данных — используем базовую эвристику
        if not skip_pages:
            # Пропускаем первую (титул) и последнюю (штамп)
            return list(range(1, total - 1))

        # Отбираем страницы, не попавшие в skip
        key_pages = []
        for page_num in range(1, total - 1):  # 1..total-2 (0-based)
            if page_num not in skip_pages:
                key_pages.append(page_num)

        logger.info(
            f"🎯 Страниц для анализа: {len(key_pages)}/{total} "
            f"(пропущено: {len(skip_pages)} — {sorted(skip_pages)})"
        )
        return key_pages if key_pages else list(range(1, total - 1))

    def _find_room_for_page(
        self, ocr_result: PDFParseResult, page_num: int
    ) -> Optional[ParsedRoom]:
        """Найти описание комнаты для страницы из OCR."""
        # Пока простая эвристика: ищем page=N в тексте комнат
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
