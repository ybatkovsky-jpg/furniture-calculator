"""
Сервис для распознавания чертежей мебели через Vision LLM.

Провайдеры (в порядке приоритета):
- Z.ai GLM-4.6V — детекция модулей с bounding boxes (основной)
- OpenRouter Qwen3-VL-235B — лучшее пространственное мышление (запасной)
- OpenRouter Gemini 2.5 Flash — fallback

GLM-4.6V уникален тем, что умеет детектировать объекты и выдавать
их координаты (bounding boxes), что идеально для мебельных чертежей.
"""

import json
import logging
import base64
import io
import time
from typing import Dict, List, Optional, Any
from pathlib import Path
from dataclasses import dataclass, field

import httpx
from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)


# ================================================================
# ДАТАКЛАССЫ
# ================================================================

@dataclass
class RecognizedModule:
    """Распознанный модуль мебели."""
    type: str          # "lower_base", "upper_base", "penal", "column", "tumbler", "corner"
    width: int         # ширина в мм
    depth: int         # глубина в мм
    height: int        # высота в мм
    quantity: int = 1
    has_glass: bool = False
    facades: Optional[Dict[str, Any]] = None
    drawers: Optional[Dict[str, Any]] = None
    shelves: int = 0
    is_corner: bool = False
    # Bounding box (если модель вернула)
    bbox: Optional[Dict[str, int]] = None  # {"x": int, "y": int, "w": int, "h": int}


@dataclass
class RecognitionResult:
    """Результат распознавания чертежа."""
    modules: List[RecognizedModule]
    confidence: str   # "high", "medium", "low"
    notes: Optional[str] = None
    zone_type: Optional[str] = None
    materials_mentioned: List[str] = field(default_factory=list)
    model_used: Optional[str] = None


# ================================================================
# ВАЛИДАЦИЯ РАЗМЕРОВ
# ================================================================

# Реалистичные диапазоны размеров мебельных модулей (мм)
DIMENSION_LIMITS = {
    "width":  (150, 2400),   # мин/макс ширина модуля
    "depth":  (200, 1200),   # мин/макс глубина
    "height": (300, 2800),   # мин/макс высота
    "quantity": (1, 30),     # мин/макс количество одинаковых модулей
}


def _validate_module(module: RecognizedModule) -> tuple:
    """
    Проверить реалистичность размеров модуля.
    Возвращает (валиден: bool, причина: str).
    """
    w, d, h, qty = module.width, module.depth, module.height, module.quantity

    if w < DIMENSION_LIMITS["width"][0] or w > DIMENSION_LIMITS["width"][1]:
        return False, f"width={w} вне [{DIMENSION_LIMITS['width'][0]}..{DIMENSION_LIMITS['width'][1]}] мм"
    if d < DIMENSION_LIMITS["depth"][0] or d > DIMENSION_LIMITS["depth"][1]:
        return False, f"depth={d} вне [{DIMENSION_LIMITS['depth'][0]}..{DIMENSION_LIMITS['depth'][1]}] мм"
    if h < DIMENSION_LIMITS["height"][0] or h > DIMENSION_LIMITS["height"][1]:
        return False, f"height={h} вне [{DIMENSION_LIMITS['height'][0]}..{DIMENSION_LIMITS['height'][1]}] мм"
    if qty < DIMENSION_LIMITS["quantity"][0] or qty > DIMENSION_LIMITS["quantity"][1]:
        return False, f"quantity={qty} вне [{DIMENSION_LIMITS['quantity'][0]}..{DIMENSION_LIMITS['quantity'][1]}]"

    # Угловой модуль должен быть квадратным
    if module.is_corner and w != d:
        return False, f"угловой модуль не квадратный: {w}×{d}"

    # Высота должна соответствовать типу
    if module.type == "lower_base" and h > 1000:
        return False, f"нижняя база слишком высокая: {h} мм (ожидается ≤1000)"
    if module.type == "upper_base" and h > 1200:
        return False, f"верхняя база слишком высокая: {h} мм (ожидается ≤1200)"
    if module.type == "penal" and h < 1500:
        return False, f"пенал слишком низкий: {h} мм (ожидается ≥1500)"

    return True, ""


# ================================================================
# ПРОМПТЫ
# ================================================================

# Унифицированный промпт с few-shot примерами (основной для всех моделей)
UNIFIED_PROMPT = """Ты — парсер мебельных чертежей. Извлеки ВСЕ модули в JSON. Никакого текста вне JSON.

ТИПЫ МОДУЛЕЙ:
- lower_base: напольный (высота 700-900мм, глубина 500-600мм)
- upper_base: навесной (высота 600-1000мм, глубина 280-350мм)
- penal: высокий шкаф от пола (высота 1800-2500мм)
- corner: угловой, КВАДРАТНЫЙ (ширина=глубина, 600×600 или 900×900). ОДИН модуль!

ПРИМЕР 1 — прямая кухня:
{"zone_type":"kitchen","materials":["EGGER H1379"],"modules":[{"type":"lower_base","width":600,"depth":560,"height":820,"quantity":3,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":1,"is_corner":false},{"type":"upper_base","width":600,"depth":320,"height":720,"quantity":2,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":1,"is_corner":false}],"confidence":"high","notes":""}

ПРИМЕР 2 — угловая кухня:
{"zone_type":"kitchen","materials":["EGGER H1379","МДФ"],"modules":[{"type":"corner","width":900,"depth":900,"height":820,"quantity":1,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":0,"is_corner":true},{"type":"lower_base","width":600,"depth":560,"height":820,"quantity":2,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":1},"shelves":1,"is_corner":false}],"confidence":"high","notes":""}

ПРАВИЛА:
- Размеры ТОЛЬКО в мм, целые числа
- УГЛОВОЙ модуль — ВСЕГДА width=depth, is_corner=true
- Если на чертеже указано ×3 — quantity=3
- Если размеров нет — стандарт: низ 560×820, верх 320×720, пенал 560×2100
- JSON в двойных кавычках, без trailing commas
- confidence: high (чёткие размеры) / medium (часть размеров неясна) / low (только контуры)"""


# Детальный промпт для повторной попытки при low confidence
DETAILED_PROMPT = """Ты — эксперт по чтению мебельных чертежей. На изображении ЧЕРТЁЖ — изучи его ВНИМАТЕЛЬНО.

ШАГ 1. Перечисли ВСЕ размеры, которые ты ВИДИШЬ на чертеже (даже если неуверен):
- Каждое число с единицей измерения (мм, cm, м)
- Каждую размерную линию (стрелки, засечки)

ШАГ 2. Для КАЖДОГО обнаруженного размера — определи, к какому модулю он относится.

ШАГ 3. Выдай JSON с модулями.

Типы модулей:
- lower_base: напольный, высота 700-900мм, глубина 500-600мм
- upper_base: навесной, глубина 280-350мм
- penal: высокий от пола, 1800-2500мм
- corner: КВАДРАТНЫЙ угловой, width=depth

Если размер не указан — НЕ додумывай, используй стандарт:
- Нижние: 560×820, верхние: 320×720, пеналы: 560×2100

Формат — ТОЛЬКО JSON с полями: zone_type, materials, modules (type, width, depth, height, quantity, has_glass, facades, drawers, shelves, is_corner), confidence, notes."""


# Сохраняем старые промпты для обратной совместимости
GLM46V_PROMPT = UNIFIED_PROMPT
QWEN3_VL_PROMPT = UNIFIED_PROMPT
GEMINI_PROMPT = UNIFIED_PROMPT


# ================================================================
# ОСНОВНОЙ КЛАСС
# ================================================================

class GeminiImageAnalyzer:
    """
    Анализатор изображений чертежей через Vision LLM.

    Провайдеры (по порядку):
    1. Z.ai GLM-4.6V — object detection + bounding boxes
    2. OpenRouter Qwen3-VL-235B — пространственное мышление
    3. OpenRouter Qwen3-VL-32B — быстрый fallback
    4. OpenRouter Gemini 2.5 Flash — запасной
    """

    FALLBACK_CHAIN = [
        ("glm-4.6v", "zai"),            # быстрый, но нестабильный
    ]

    def __init__(self):
        """Инициализация HTTP клиента."""
        self.zai_key = settings.zai_api_key
        self.openrouter_key = settings.openrouter_api_key
        self.primary_model = settings.vision_model
        self.api_url = settings.vision_api_url

        self.client: Optional[httpx.AsyncClient] = None

        logger.info(
            f"Vision Analyzer: primary={self.primary_model}, "
            f"url={self.api_url.split('/api')[0] if '/api' in self.api_url else self.api_url}"
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(180.0))
        return self.client

    async def close(self):
        if self.client:
            await self.client.aclose()
            self.client = None

    # -----------------------------------------------------------
    # ПУБЛИЧНЫЙ МЕТОД
    # -----------------------------------------------------------

    async def analyze_drawing(
        self,
        image_path: str | Path,
        max_retries: int = 4
    ) -> RecognitionResult:
        """
        Распознать модули мебели на чертеже.
        
        Стратегия:
        1. Основная модель → первичный результат
        2. Если confidence low → повтор с DETAILED_PROMPT
        3. Если всё ещё low → ансамбль с fallback-моделью + кросс-валидация
        """
        # Строим цепочку: основная модель + fallback
        models_to_try = [(self.primary_model, self._detect_provider(self.primary_model))]
        for model, provider in self.FALLBACK_CHAIN:
            if model != self.primary_model:
                models_to_try.append((model, provider))

        last_error = None

        # ── Шаг 1: Основная модель ──
        for attempt, (model, provider) in enumerate(models_to_try[:max_retries]):
            try:
                logger.info(f"Попытка #{attempt + 1}: {model} ({provider})")

                result = await self._try_analyze(image_path, model, provider)

                if result.modules and len(result.modules) > 0:
                    result.model_used = model
                    logger.info(
                        f"✅ Успех: model={model}, modules={len(result.modules)}, "
                        f"confidence={result.confidence}, zone={result.zone_type}"
                    )

                    # ── Шаг 2: Low confidence → детальный промпт ──
                    if result.confidence == "low":
                        logger.info("🔄 Low confidence — пробуем детальный промпт...")
                        detail_result = await self._try_analyze_with_prompt(
                            image_path, model, provider, DETAILED_PROMPT
                        )
                        if detail_result.modules and len(detail_result.modules) > 0:
                            # Выбираем результат с бóльшим числом модулей
                            if len(detail_result.modules) >= len(result.modules):
                                result = detail_result
                                result.model_used = f"{model} (detailed)"
                                logger.info(
                                    f"✅ Детальный промпт: {len(result.modules)} модулей"
                                )
                            else:
                                logger.info("Оставлен результат основного промпта (больше модулей)")

                    # ── Шаг 3: Ансамбль — вторая модель для кросс-валидации ──
                    if result.confidence in ("low", "medium") and len(models_to_try) > 1:
                        result = await self._ensemble_validate(
                            result, image_path, model, models_to_try
                        )

                    return result
                else:
                    logger.warning(f"Модель {model} вернула пустой результат")

            except Exception as e:
                last_error = e
                logger.error(
                    f"Ошибка {model}: {type(e).__name__}: {e}",
                    exc_info=(attempt == 0)
                )
                if attempt < min(len(models_to_try), max_retries) - 1:
                    delay = 2 * (attempt + 1)
                    logger.info(f"Ожидание {delay}с...")
                    time.sleep(delay)

        logger.error(f"Все попытки не удались. Последняя ошибка: {last_error}")
        return RecognitionResult(
            modules=[],
            confidence="low",
            notes=f"Не удалось распознать после {max_retries} попыток. "
                  f"Попробуйте другой ракурс или введите модули вручную.",
            model_used=None,
        )

    # -----------------------------------------------------------
    # ОДНА ПОПЫТКА
    # -----------------------------------------------------------

    async def _try_analyze(
        self,
        image_path: str | Path,
        model: str,
        provider: str
    ) -> RecognitionResult:
        """Одна попытка распознавания с конкретной моделью и провайдером."""
        client = await self._get_client()

        # 1. Загружаем и оптимизируем изображение
        image = Image.open(image_path)
        logger.info(f"Изображение: {image.size}, mode={image.mode}")

        # 1a. Повышаем контраст — цифры на чертеже читаются лучше
        from PIL import ImageEnhance, ImageFilter
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(1.3)  # +30% контраст

        # 1b. Лёгкое повышение резкости — границы размерных линий чётче
        image = image.filter(ImageFilter.SHARPEN)

        # Уменьшаем если больше 2048px (GLM-4.6V оптимально)
        max_size = 2048
        if image.width > max_size or image.height > max_size:
            ratio = min(max_size / image.width, max_size / image.height)
            image = image.resize(
                (int(image.width * ratio), int(image.height * ratio)),
                Image.Resampling.LANCZOS
            )
            logger.info(f"Уменьшено до: {image.size}")

        # 2. Конвертируем в base64 JPEG
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=85)
        image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        logger.info(f"Base64: {len(image_base64)} chars")

        # 3. Выбираем промпт
        prompt = self._select_prompt(model)

        # 4. Формируем запрос (OpenAI-совместимый формат)
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    }
                }
            ]
        }]

        headers = {
            "Authorization": f"Bearer {self._get_api_key(provider)}",
            "Content-Type": "application/json",
        }

        # OpenRouter — дополнительные заголовки
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://pro-mebel.ru"
            headers["X-Title"] = "PRO Furniture Calculator"

        body = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 8000,              # GLM-4.6V нужно место под reasoning + JSON
            "reasoning_effort": "medium",     # medium = быстрее, но всё ещё думает
            "response_format": {"type": "json_object"},  # гарантирует валидный JSON
        }

        # 5. Определяем URL эндпоинта
        url = self._get_api_url(provider, model)

        # 6. Отправляем запрос
        response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()
        response_data = response.json()

        # 7. Извлекаем текст ответа (с учётом thinking mode)
        msg = response_data["choices"][0]["message"]
        response_text = msg.get("content", "") or msg.get("reasoning_content", "") or ""
        if not response_text:
            raise ValueError(f"Empty response from {model}. Message keys: {list(msg.keys())}")
        logger.info(f"Ответ ({model[:30]}...): {response_text[:400]}...")

        # 8. Парсим JSON
        cleaned_text = self._extract_json(response_text)
        if not cleaned_text or cleaned_text in ('{}', ''):
            raise ValueError(
                f"No JSON found in response. "
                f"First 200 chars: {response_text[:200]}"
            )
        try:
            result_data = json.loads(cleaned_text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error at pos {e.pos}: {cleaned_text[max(0,e.pos-50):e.pos+50]}")
            raise

        # 9. Преобразуем в RecognitionResult
        result = self._parse_result(result_data)

        # 10. Постобработка
        result = self._postprocess_results(result)

        return result

    # -----------------------------------------------------------
    # АНСАМБЛЬ И ПОВТОРНЫЕ ПОПЫТКИ
    # -----------------------------------------------------------

    async def _try_analyze_with_prompt(
        self,
        image_path: str | Path,
        model: str,
        provider: str,
        prompt_override: str,
    ) -> RecognitionResult:
        """
        Одна попытка распознавания с ПЕРЕОПРЕДЕЛЁННЫМ промптом.
        Используется для повторной попытки с детальным промптом.
        """
        client = await self._get_client()

        # Загружаем и оптимизируем изображение (та же логика)
        image = Image.open(image_path)
        from PIL import ImageEnhance, ImageFilter
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(1.3)
        image = image.filter(ImageFilter.SHARPEN)

        max_size = 2048
        if image.width > max_size or image.height > max_size:
            ratio = min(max_size / image.width, max_size / image.height)
            image = image.resize(
                (int(image.width * ratio), int(image.height * ratio)),
                Image.Resampling.LANCZOS
            )

        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=85)
        image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_override},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]
        }]

        headers = {
            "Authorization": f"Bearer {self._get_api_key(provider)}",
            "Content-Type": "application/json",
        }
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://pro-mebel.ru"
            headers["X-Title"] = "PRO Furniture Calculator"

        body = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 8000,
            "reasoning_effort": "medium",
            "response_format": {"type": "json_object"},
        }

        url = self._get_api_url(provider, model)
        response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()
        response_data = response.json()

        msg = response_data["choices"][0]["message"]
        response_text = msg.get("content", "") or msg.get("reasoning_content", "") or ""
        if not response_text:
            raise ValueError(f"Empty response from {model}")

        cleaned_text = self._extract_json(response_text)
        if not cleaned_text or cleaned_text in ('{}', ''):
            raise ValueError(f"No JSON in response: {response_text[:200]}")

        result_data = json.loads(cleaned_text)
        result = self._parse_result(result_data)
        result = self._postprocess_results(result)
        result.model_used = f"{model} (custom prompt)"
        return result

    async def _ensemble_validate(
        self,
        primary_result: RecognitionResult,
        image_path: str | Path,
        primary_model: str,
        models_to_try: list,
    ) -> RecognitionResult:
        """
        Кросс-валидация второй моделью (ансамбль).
        
        Запрашивает fallback-модель, сравнивает результаты.
        Если модели сошлись — повышает confidence.
        Если разошлись — добавляет предупреждение.
        """
        # Выбираем fallback-модель (не основную)
        fallback = None
        for m, p in models_to_try:
            if m != primary_model:
                fallback = (m, p)
                break

        if not fallback:
            logger.info("Нет fallback-модели для ансамбля")
            return primary_result

        fallback_model, fallback_provider = fallback
        logger.info(f"🎯 Ансамбль: {fallback_model} для кросс-валидации...")

        try:
            second_result = await self._try_analyze(image_path, fallback_model, fallback_provider)
        except Exception as e:
            logger.warning(f"Ансамбль: ошибка fallback-модели: {e}")
            return primary_result

        if not second_result.modules:
            logger.info("Ансамбль: fallback не нашёл модулей — оставляем primary")
            return primary_result

        # Сравниваем модули по типу и размерам (допуск ±50мм по ширине, ±20мм по глубине)
        primary_modules = primary_result.modules
        second_modules = second_result.modules

        matched_primary = set()
        matched_second = set()

        for i, m1 in enumerate(primary_modules):
            for j, m2 in enumerate(second_modules):
                if j in matched_second:
                    continue
                if (m1.type == m2.type
                        and abs(m1.width - m2.width) <= 50
                        and abs(m1.depth - m2.depth) <= 20):
                    matched_primary.add(i)
                    matched_second.add(j)
                    break

        overlap = len(matched_primary) / max(len(primary_modules), 1)
        logger.info(
            f"🎯 Ансамбль: primary={len(primary_modules)}, "
            f"fallback={len(second_modules)}, overlap={overlap:.0%}"
        )

        if overlap >= 0.7:
            primary_result.confidence = "high"
            primary_result.notes = (primary_result.notes or "") + " | Ансамбль: высокая сходимость ✓"
            logger.info("🎯 Ансамбль: высокая сходимость → confidence=high")
        elif overlap >= 0.4:
            primary_result.confidence = "medium"
            primary_result.notes = (primary_result.notes or "") + " | Ансамбль: средняя сходимость"
            # Добавляем модули из fallback, которых нет в primary
            for j, m2 in enumerate(second_modules):
                if j not in matched_second:
                    primary_modules.append(m2)
                    logger.info(f"  + добавлен модуль из fallback: {m2.type} {m2.width}×{m2.depth}×{m2.height}")
        else:
            primary_result.confidence = "low"
            primary_result.notes = (primary_result.notes or "") + " | ⚠ Ансамбль: модели расходятся — нужна проверка оператора"
            # Добавляем все fallback-модули как дополнительные
            for j, m2 in enumerate(second_modules):
                if j not in matched_second:
                    primary_modules.append(m2)

        primary_result.model_used = f"{primary_result.model_used} + {fallback_model} (ensemble)"
        return primary_result

    # -----------------------------------------------------------
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # -----------------------------------------------------------

    def _detect_provider(self, model: str) -> str:
        """Определить провайдера по имени модели."""
        if "glm" in model.lower():
            return "zai"
        if "qwen" in model.lower() or "google" in model.lower():
            return "openrouter"
        return "zai"  # по умолчанию

    def _get_api_key(self, provider: str) -> str:
        """Получить API ключ для провайдера."""
        if provider == "zai":
            return self.zai_key or self.openrouter_key or settings.gemini_api_key
        elif provider == "openrouter":
            return self.openrouter_key or self.zai_key or settings.gemini_api_key
        return self.zai_key or self.openrouter_key or settings.gemini_api_key

    def _get_api_url(self, provider: str, model: str) -> str:
        """Получить URL эндпоинта."""
        if provider == "zai":
            return "https://api.z.ai/api/paas/v4/chat/completions"
        elif provider == "openrouter":
            return "https://openrouter.ai/api/v1/chat/completions"
        return self.api_url

    def _select_prompt(self, model: str) -> str:
        """Выбрать оптимальный промпт под модель."""
        m = model.lower()
        if "glm" in m:
            return GLM46V_PROMPT
        elif "qwen" in m:
            return QWEN3_VL_PROMPT
        else:
            return GEMINI_PROMPT

    def _extract_json(self, text: str) -> str:
        """
        Извлечь JSON из ответа модели.
        GLM-4.6V выводит reasoning перед JSON — ищем JSON с конца текста.
        """
        import re

        cleaned = text.strip()

        # 1. Ищем ```json ``` блок (приоритет)
        match = re.search(r'```json\s*(.*?)\s*```', cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()
            logger.debug("Found JSON in ```json block")
            return self._fix_json_syntax(cleaned)

        # 2. Ищем с КОНЦА: GLM-4.6V пишет reasoning, потом JSON
        #    Идём справа налево, отслеживая скобки
        depth = 0
        json_end = len(cleaned)
        json_start = -1

        for i in range(len(cleaned) - 1, -1, -1):
            ch = cleaned[i]
            if ch == '}':
                depth += 1
                if depth == 1 and json_end == len(cleaned):
                    json_end = i + 1
            elif ch == '{':
                depth -= 1
                if depth == 0:
                    json_start = i
                    break

        if json_start >= 0 and json_end > json_start:
            candidate = cleaned[json_start:json_end]
            if '"modules"' in candidate or "'modules'" in candidate:
                logger.debug(f"Found JSON with modules at end ({len(candidate)} chars)")
                return self._fix_json_syntax(candidate)

        # 3. Fallback: просто ищем любой JSON с "modules"
        for candidate in re.findall(r'\{[^{}]*"modules"[^{}]*\}', cleaned):
            logger.debug(f"Found modules-JSON via regex")
            return self._fix_json_syntax(candidate)

        # 4. Последняя попытка: от первой { до последней }
        json_start = cleaned.find('{')
        json_end = cleaned.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            logger.debug("Fallback: first { to last }")
            return self._fix_json_syntax(cleaned[json_start:json_end])

        return cleaned

    def _fix_json_syntax(self, json_str: str) -> str:
        """Починить JS-стиль JSON: ключи без кавычек, trailing commas."""
        import re
        # Ключи без кавычек: {key: val} → {"key": val}
        json_str = re.sub(
            r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:',
            r'\1"\2":',
            json_str
        )
        # Trailing commas
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)
        return json_str

    def _parse_result(self, data: Dict[str, Any]) -> RecognitionResult:
        """Преобразовать JSON-ответ модели в RecognitionResult."""
        modules = []

        for module_data in data.get("modules", []):
            try:
                module_type = module_data.get("type", "lower_base")
                w = module_data.get("width", 0)
                d = module_data.get("depth", 0)

                is_corner = (
                    module_type == "corner"
                    or module_data.get("is_corner", False)
                    or (module_type in ("lower_base", "upper_base")
                        and w == d and w in (600, 900, 1000))
                )

                bbox = None
                if "bbox" in module_data:
                    bbox = module_data["bbox"]
                elif all(k in module_data for k in ("x", "y", "w", "h")):
                    bbox = {
                        "x": module_data["x"], "y": module_data["y"],
                        "w": module_data["w"], "h": module_data["h"]
                    }

                module = RecognizedModule(
                    type="corner" if is_corner else module_type,
                    width=int(w) if w else 0,
                    depth=int(d) if d else 0,
                    height=int(module_data.get("height", 0)),
                    quantity=module_data.get("quantity", 1),
                    has_glass=module_data.get("has_glass", False),
                    facades=module_data.get("facades"),
                    drawers=module_data.get("drawers"),
                    shelves=module_data.get("shelves", 0),
                    is_corner=is_corner,
                    bbox=bbox,
                )
                # Валидация размеров
                is_valid, reason = _validate_module(module)
                if not is_valid:
                    logger.warning(f"⚠️  Пропущен модуль: {reason}, data={module_data}")
                    continue
                modules.append(module)
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"Parse error for module: {e}, data={module_data}")

        return RecognitionResult(
            modules=modules,
            confidence=data.get("confidence", "low"),
            notes=data.get("notes"),
            zone_type=data.get("zone_type"),
            materials_mentioned=data.get("materials", []),
        )

    def _postprocess_results(self, result: RecognitionResult) -> RecognitionResult:
        """Постобработка: убираем дубликаты, фиксим ошибки классификации."""
        if not result.modules:
            return result

        logger.info(
            f"Pre-process ({len(result.modules)}): "
            f"{[f'{m.type} {m.width}×{m.depth}×{m.height}' for m in result.modules]}"
        )

        corners = []
        regular = []

        for module in result.modules:
            if module.is_corner or module.type == "corner":
                if module.width == module.depth and module.width in (600, 900, 1000):
                    corners.append(module)
                else:
                    module.type = "upper_base"
                    module.is_corner = False
                    regular.append(module)
                    logger.warning(f"Non-square corner {module.width}×{module.depth} → upper_base")
            else:
                regular.append(module)

        # Дедупликация угловых
        unique_corners = {}
        for c in corners:
            key = f"{c.width}_{c.depth}"
            unique_corners[key] = c

        processed = list(unique_corners.values())

        # Группировка одинаковых обычных модулей
        groups: Dict[str, List[RecognizedModule]] = {}
        for m in regular:
            key = f"{m.type}_{m.width}_{m.depth}_{m.height}"
            groups.setdefault(key, []).append(m)

        for key, mods in groups.items():
            if len(mods) == 1:
                processed.append(mods[0])
            else:
                total_qty = sum(m.quantity for m in mods)
                merged = RecognizedModule(
                    type=mods[0].type,
                    width=mods[0].width,
                    depth=mods[0].depth,
                    height=mods[0].height,
                    quantity=total_qty,
                    has_glass=mods[0].has_glass,
                    facades=mods[0].facades,
                    drawers=mods[0].drawers,
                    shelves=mods[0].shelves,
                    is_corner=mods[0].is_corner,
                    bbox=mods[0].bbox,
                )
                processed.append(merged)
                logger.info(f"Merged {len(mods)}× {key} → qty={total_qty}")

        logger.info(f"Post-process: {len(result.modules)} → {len(processed)}")

        return RecognitionResult(
            modules=processed,
            confidence=result.confidence,
            notes=result.notes,
            zone_type=result.zone_type,
            materials_mentioned=result.materials_mentioned,
        )


# ================================================================
# ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР
# ================================================================

_analyzer: Optional[GeminiImageAnalyzer] = None


def get_analyzer() -> GeminiImageAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = GeminiImageAnalyzer()
    return _analyzer
