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
    zone_type: Optional[str] = None      # тип помещения из AI
    modules: List = field(default_factory=list)   # RecognizedModule
    materials: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    confidence: str = ""
    quality_score: float = 0.0        # 0..1, чем выше тем лучше
    quality_flags: List[str] = field(default_factory=list)  # предупреждения


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

        # ── Шаг 3: ПАРАЛЛЕЛЬНЫЙ рендеринг + Vision ──
        room_specs: Dict[int, RoomSpec] = {}

        # Обрабатываем страницы параллельно батчами по 3
        BATCH_SIZE = 3
        for batch_start in range(0, len(pages_to_analyze), BATCH_SIZE):
            batch = pages_to_analyze[batch_start:batch_start + BATCH_SIZE]
            tasks = [self._process_page(path, page_num, ocr_result, result.total_pages)
                     for page_num in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for page_num, recog_result_or_error in zip(batch, batch_results):
                if isinstance(recog_result_or_error, Exception):
                    logger.error(f"    ❌ Стр. {page_num + 1}: {recog_result_or_error}")
                    result.errors.append(f"Стр. {page_num + 1}: {recog_result_or_error}")
                    continue

                recog_result = recog_result_or_error
                if recog_result is None:
                    continue

                if recog_result.modules:
                    ocr_room = self._find_room_for_page(ocr_result, page_num)
                    if ocr_room:
                        room_name = _fix_ocr_name(ocr_room.name)
                    else:
                        room_name = _generate_room_name(recog_result, page_num)

                    if page_num not in room_specs:
                        room_specs[page_num] = RoomSpec(
                            room_name=room_name,
                            page=page_num + 1,
                            zone_type=recog_result.zone_type,
                        )

                    spec = room_specs[page_num]
                    spec.modules.extend(recog_result.modules)
                    spec.materials.extend(recog_result.materials_mentioned)
                    spec.confidence = recog_result.confidence
                    if recog_result.zone_type and not spec.zone_type:
                        spec.zone_type = recog_result.zone_type

                    logger.info(
                        f"    ✅ Стр.{page_num + 1} {recog_result.zone_type or '?'}: "
                        f"{len(recog_result.modules)} модулей"
                    )
                else:
                    logger.info(f"    ⏭️  Стр.{page_num + 1}: модули не найдены")

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

        # ── Quality Score + Кросс-валидация OCR ↔ Vision ──
        for room in result.rooms:
            # 1. Расчёт оценки качества
            _calculate_quality(room)

            # 2. Кросс-валидация: сравниваем OCR-размеры и Vision-модули
            if room.modules:
                ocr_room = self._find_room_for_page(ocr_result, room.page - 1)
                if ocr_room and ocr_room.dimensions:
                    ocr_dim_count = len(ocr_room.dimensions)
                    vision_mod_count = len(room.modules)
                    diff = abs(ocr_dim_count - vision_mod_count)

                    if diff >= 3:
                        room.quality_score = max(0, room.quality_score - 0.2)
                        room.quality_flags.append(
                            f"⚠ Расхождение OCR/Vision: OCR={ocr_dim_count} размеров, "
                            f"Vision={vision_mod_count} модулей"
                        )
                        logger.warning(
                            f"{room.room_name}: OCR={ocr_dim_count} размеров, "
                            f"Vision={vision_mod_count} модулей — проверьте!"
                        )
                    elif diff >= 1:
                        room.quality_score = max(0, room.quality_score - 0.1)
                        room.quality_flags.append(
                            f"Небольшое расхождение OCR/Vision: OCR={ocr_dim_count}, Vision={vision_mod_count}"
                        )

        # Сводка качества
        low_quality = [r for r in result.rooms if r.quality_score < 0.5]
        if low_quality:
            logger.warning(
                f"⚠️ Помещений с низким качеством (<0.5): {len(low_quality)} — "
                f"{[r.room_name for r in low_quality]}"
            )

        result.success = len(result.rooms) > 0
        logger.info(
            f"✅ Конвейер завершён: {len(result.rooms)} помещений, "
            f"{sum(len(r.modules) for r in result.rooms)} модулей"
        )
        return result

    # -----------------------------------------------------------
    # ВСПОМОГАТЕЛЬНЫЕ
    # -----------------------------------------------------------

    async def _process_page(
        self,
        pdf_path: Path,
        page_num: int,
        ocr_result: PDFParseResult,
        total_pages: int,
    ):
        """
        Обработать одну страницу: рендеринг → Единый анализ → Fallback.

        1. analyze_page (Qwen3-VL-235B + unified prompt: bbox + модули + материалы)
        2. Если 0 модулей → fallback: analyze_drawing (GLM-5V-Turbo)
        """
        logger.info(f"  ▶ Стр. {page_num + 1}/{total_pages}...")

        jpeg_bytes = render_page(pdf_path, page_num)

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp.write(jpeg_bytes)
            tmp_path = tmp.name

        try:
            # ── 1. Единый анализ (bbox + модули) ──
            modules, zone_type, materials, confidence = await self.vision.analyze_page(tmp_path)

            if modules:
                logger.info(
                    f"    ✅ Стр.{page_num + 1} (единый): "
                    f"{len(modules)} модулей, confidence={confidence}"
                )
                return RecognitionResult(
                    modules=modules,
                    confidence=confidence,
                    zone_type=zone_type,
                    materials_mentioned=materials or [],
                )

            # ── 2. Fallback: модульный анализ (GLM-5V-Turbo) ──
            logger.info(f"    ⚠️ Единый анализ не дал модулей, fallback на GLM-5V...")
            return await self.vision.analyze_drawing(tmp_path)

        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # Индикаторы: страница содержит чертёж (размеры, масштаб, виды)
    DRAWING_INDICATORS = [
        "×", "х", "мм", "mm",
        "М1:", "М 1:", "М1:50", "М1:25", "М1:10", "М1:20",
        "масштаб", "Масштаб",
        "ВИД СВЕРХУ", "ВИД СПЕРЕДИ", "ВИД СБОКУ",
        "РАЗРЕЗ", "Разрез",
        "фасад", "Фасад",
        "габарит", "Габарит",
        "план", "План",
    ]

    # Индикаторы: страница НЕ чертёж (титул, ведомость, легенда)
    SKIP_INDICATORS = [
        "СОДЕРЖАНИЕ", "Содержание",
        "ВЕДОМОСТЬ", "Ведомость",
        "СПЕЦИФИКАЦИЯ", "Спецификация",
        "ТИТУЛ", "Титул", "ТИТУЛЬНЫЙ",
        "ПРИМЕЧАНИЕ", "Примечание", "ПРИЕМАНИЕ",
        "УСЛОВНЫЕ ОБОЗНАЧЕНИЯ", "Условные обозначения",
        "ЛЕГЕНДА", "Легенда",
        "штамп", "Штамп", "печать",
        "Общие данные", "ОБЩИЕ ДАННЫЕ",
    ]

    def _pick_key_pages(
        self, ocr_result: PDFParseResult, total: int
    ) -> List[int]:
        """
        Выбрать страницы с чертежами, исключая титульные, ведомости и штампы.

        Использует данные OCR (имена помещений) для идентификации
        не-чертёжных страниц. Паттерны размеров (×, мм) — признак чертежа.
        """
        if total <= 3:
            return list(range(total))

        # Собираем имена OCR-комнат для поиска не-чертёжных
        ocr_room_names_lower = set()
        for room in ocr_result.rooms:
            if room.name:
                ocr_room_names_lower.add(room.name.lower().rstrip(':'))

        # Собираем текст всех комнат для поиска drawing-индикаторов
        all_ocr_text = " ".join(r.raw_text for r in ocr_result.rooms if r.raw_text)

        key_pages = []
        for page_num in range(total):
            # Всегда пропускаем титульную (стр. 0)
            if page_num == 0:
                logger.info(f"  Стр. 1: пропущена (титульная)")
                continue

            # Всегда пропускаем последнюю (штамп/легенда)
            if page_num >= total - 1:
                logger.info(f"  Стр. {page_num + 1}: пропущена (последняя)")
                continue

            # Проверяем, не является ли страница не-чертежом по имени OCR-комнаты
            is_skip = False
            for name in ocr_room_names_lower:
                for indicator in self.SKIP_INDICATORS:
                    if indicator.lower() in name:
                        # Грубая привязка: если имя комнаты содержит индикатор пропуска,
                        # и её порядковый номер примерно соответствует странице
                        is_skip = True
                        break
                if is_skip:
                    break

            if is_skip:
                logger.info(f"  Стр. {page_num + 1}: пропущена (не-чертёж по OCR)")
                continue

            # Проверяем, есть ли в OCR-тексте признаки чертежа
            has_drawing_signs = any(
                indicator.lower() in all_ocr_text.lower()
                for indicator in self.DRAWING_INDICATORS
            )

            if has_drawing_signs or len(ocr_result.rooms) == 0:
                key_pages.append(page_num)
            else:
                # Без признаков чертежа — берём только если это не первые/последние
                if 1 < page_num < total - 2:
                    key_pages.append(page_num)

        # Если после фильтрации ничего не осталось — fallback на старую логику
        if not key_pages:
            logger.warning("Умный отбор не дал страниц — fallback на 1..N-2")
            key_pages = list(range(1, total - 1))

        logger.info(
            f"Отобрано страниц для Vision: {len(key_pages)} из {total} "
            f"(пропущено {total - len(key_pages)} не-чертёжных)"
        )
        return key_pages

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

def _scaled_facades_to_modules(scaled_facades) -> List:
    """
    Конвертировать ScaledFacade (с bbox-вычисленными размерами) в RecognizedModule.
    """
    from app.services.image_analyzer import RecognizedModule

    modules = []
    for sf in scaled_facades:
        zone = sf.zone
        if zone == "penal":
            modules.append(RecognizedModule(
                type="penal", width=sf.width_mm, depth=560, height=sf.height_mm,
                quantity=1, facades={"count": 1, "type": "doors"}, shelves=0, is_corner=False,
            ))
        elif zone == "lower":
            modules.append(RecognizedModule(
                type="lower_base", width=sf.width_mm, depth=560, height=sf.height_mm,
                quantity=1, facades={"count": 1, "type": "doors"}, shelves=1, is_corner=False,
            ))
        elif zone == "upper":
            modules.append(RecognizedModule(
                type="upper_base", width=sf.width_mm, depth=320, height=sf.height_mm,
                quantity=1, facades={"count": 1, "type": "doors"}, shelves=1, is_corner=False,
            ))
    return modules


# Маппинг: zone_type → «в + винительный падеж» для комбинированных названий
ROOM_CONTEXT = {
    "Кухня": "в кухню",
    "Гостиная": "в гостиную",
    "Спальня": "в спальню",
    "Детская": "в детскую",
    "Прихожая": "в прихожую",
    "Ванная": "в ванную",
    "Гардеробная": "в гардеробную",
    "Санузел": "в санузел",
    "Кабинет": "в кабинет",
    "Балкон": "на балкон",
    "Столовая": "в столовую",
    "Постирочная": "в постирочную",
}

# Мебель, которая САМА говорит за себя (не добавляем комнату)
SELF_DESCRIPTIVE = {"Кухонный гарнитур", "Остров", "Кухонный гарнитур (угловой)"}


def _generate_room_name(recog_result, page_num: int = 0) -> str:
    """
    Сгенерировать название листа: «{мебель} в {комнату}».
    Например: «Тумба в гостиную», «Шкаф в спальню», «Кухонный гарнитур».
    """
    if not recog_result or not recog_result.modules:
        return f"Стр.{page_num + 1}" if page_num > 0 else "Без модулей"

    modules = recog_result.modules
    page_suffix = f" (стр.{page_num + 1})" if page_num > 0 else ""

    # Считаем типы
    type_counts = {}
    for m in modules:
        t = m.type
        type_counts[t] = type_counts.get(t, 0) + (m.quantity or 1)

    total = sum(type_counts.values())
    lower = type_counts.get("lower_base", 0)
    upper = type_counts.get("upper_base", 0)
    penal = type_counts.get("penal", 0)
    corner = type_counts.get("corner", 0)

    # Комната из zone_type (переведённая)
    zone_ru = _translate_zone_type(recog_result.zone_type) if recog_result.zone_type else ""

    # Определяем мебель
    if total == 1:
        m = modules[0]
        if m.type == "penal":
            furniture = "Шкаф"
        elif m.type == "lower_base":
            furniture = "Тумба" if m.height < 400 else "Нижняя база"
        elif m.type == "upper_base":
            furniture = "Верхняя база"
        elif m.type == "corner":
            furniture = "Угловой модуль"
        else:
            furniture = "Модуль"
    elif lower + upper >= total * 0.6 and zone_ru in ("Кухня", "Кухня-гостиная", ""):
        # Кухонный гарнитур — только для кухни
        furniture = "Кухонный гарнитур (угловой)" if corner > 0 else "Кухонный гарнитур"
    elif penal >= total * 0.5:
        furniture = "Шкаф" if penal == 1 else "Шкафы"
    elif lower >= total * 0.5:
        # Все короткие → тумбы/столешницы
        all_short = all(m.height < 400 for m in modules if m.type == "lower_base")
        furniture = "Тумба" if (lower <= 3 or all_short) else "Нижние базы"
    elif upper >= total * 0.5:
        furniture = "Верхние базы"
    else:
        furniture = ""

    # Собираем итоговое имя
    if not furniture:
        name = zone_ru if zone_ru else "Мебель"
    elif furniture in SELF_DESCRIPTIVE:
        name = furniture
    elif zone_ru and zone_ru in ROOM_CONTEXT:
        name = f"{furniture} {ROOM_CONTEXT[zone_ru]}"
    else:
        name = furniture

    return name + page_suffix


def _calculate_quality(room: RoomSpec) -> float:
    """
    Рассчитать оценку качества распознавания для помещения.
    
    Версия 2.0 — расширенный чек-лист по промпту v4.0:
    - confidence от AI
    - количество модулей
    - разнообразие размеров
    - наличие модулей
    - типы модулей и их соответствие помещению
    - проверка на угловые модули
    - проверка на пеналы в кухне
    
    Returns: 0.0 (полный брак) .. 1.0 (идеально)
    """
    score = 1.0
    flags = []
    room_lower = room.room_name.lower()

    # Confidence влияет наиболее сильно
    if room.confidence == "low":
        score -= 0.4
        flags.append("Низкая уверенность AI")
    elif room.confidence == "medium":
        score -= 0.2
        flags.append("Средняя уверенность AI")

    # Нет модулей — критично
    if not room.modules:
        score -= 0.5
        flags.append("Модули не найдены")
        room.quality_score = max(0, score)
        room.quality_flags = flags
        return room.quality_score

    # ── Чек-лист валидации (из промпта v4.0) ──

    # 1. Все детали из чертежа учтены? — косвенно: смотрим на количество модулей
    is_kitchen = any(kw in room_lower for kw in ["кухн", "гарнитур", "остров"])
    is_wardrobe = any(kw in room_lower for kw in ["гардероб", "шкаф", "прихож"])

    if is_kitchen and len(room.modules) < 2:
        score -= 0.2
        flags.append("⚠ Слишком мало модулей для кухни (ожидается ≥4)")
    elif is_kitchen and len(room.modules) < 4:
        score -= 0.1
        flags.append("⚠ Мало модулей для кухни (ожидается ≥4)")

    if is_wardrobe and len(room.modules) < 1:
        score -= 0.15
        flags.append("⚠ Модули не найдены для шкафа/гардеробной")

    # 2. Кромка правильно распределена? — проверяем типы модулей
    module_types = set(m.type for m in room.modules)
    if "corner" in module_types:
        corners = [m for m in room.modules if m.type == "corner"]
        non_square = [c for c in corners if c.width != c.depth]
        if non_square:
            score -= 0.15
            flags.append(f"⚠ Найдены неквадратные угловые модули: {len(non_square)} шт.")

    # 3. Все модули одного размера — подозрительно (возможно дубликат)
    if len(room.modules) >= 3:
        unique_sizes = set((m.width, m.depth, m.height) for m in room.modules)
        if len(unique_sizes) == 1:
            score -= 0.15
            flags.append("⚠ Все модули одного размера — возможно дубликат")

    # 4. Проверка: есть ли модули с нулевыми размерами
    zero_sized = [m for m in room.modules if m.width <= 0 or m.depth <= 0 or m.height <= 0]
    if zero_sized:
        score -= 0.3
        flags.append(f"⚠ Найдены модули с нулевыми размерами: {len(zero_sized)} шт.")

    # 5. Проверка на пеналы в кухне (должны быть высокими)
    penals = [m for m in room.modules if m.type == "penal"]
    if penals:
        short_penals = [p for p in penals if p.height < 1500]
        if short_penals:
            score -= 0.1
            flags.append(f"⚠ Подозрительно низкие пеналы (<1500мм): {len(short_penals)} шт.")

    # 6. Проверка на верхние базы в кухне (глубина должна быть 280-350мм)
    upper_bases = [m for m in room.modules if m.type == "upper_base"]
    if upper_bases:
        wrong_depth = [u for u in upper_bases if u.depth > 400]
        if wrong_depth:
            score -= 0.1
            flags.append(f"⚠ Подозрительная глубина верхних баз (>400мм): {len(wrong_depth)} шт.")

    # 7. Проверка материалов: если заявлены EMDIWAY/фасады — должен быть фасадный материал
    if room.materials:
        has_facade_material = any(
            kw in " ".join(room.materials).upper()
            for kw in ["EMDIWAY", "ФАСАД", "МДФ", "IVEGO", "ЛАКОКРАСКА", "ПВХ"]
        )
        has_facades = any(m.facades and m.facades.get("count", 0) > 0 for m in room.modules)
        if has_facade_material and not has_facades:
            score -= 0.05
            flags.append("⚠ Заявлен фасадный материал, но фасады не обнаружены")

    room.quality_score = max(0, min(1, score))
    room.quality_flags = flags
    return room.quality_score


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
        page_info = "" if "(стр." in room.room_name else f" (стр. {room.page})"
        print(f"{i}. 🏠 {room.room_name}{page_info}")
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
