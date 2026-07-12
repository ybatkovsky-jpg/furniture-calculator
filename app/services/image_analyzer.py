"""
Сервис для распознавания чертежей мебели через Vision LLM.

Провайдеры (в порядке приоритета):
- Z.ai GLM-5V-Turbo — основной (Vision API с json_object)
- RouterAI.ru GLM-4.6V — быстрый fallback (ru-агрегатор, оплата в ₽)
- RouterAI.ru Qwen3-VL-32B — ансамбль / кросс-валидация (SpatialBench SOTA)
- RouterAI.ru Gemini 2.5 Flash Lite — последний рубеж

Все модели используют унифицированный промпт с few-shot примерами.
Включён response_format: json_object для гарантированного JSON на выходе.
Добавлена валидация размеров модулей (DIMENSION_LIMITS).
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
# ПРОМПТЫ
# ================================================================

# УНИФИЦИРОВАННЫЙ ПРОМПТ — один для всех моделей
# С `response_format: json_object` модель ГАРАНТИРОВАННО вернёт JSON.
# Промпт фокусируется на ТОЧНОСТИ, а не на формате.
UNIFIED_PROMPT = """Ты — конструктор-технолог мебельной фабрики. Проанализируй чертёж и извлеки ВСЕ мебельные модули в JSON.

ТИПЫ МОДУЛЕЙ (строго):
- lower_base — напольный модуль (стоит на полу, высота 700-900мм, глубина 500-600мм)
- upper_base — навесной модуль (висит на стене, высота 600-1000мм, глубина 280-350мм)
- penal — высокий шкаф от пола до потолка (высота 1800-2500мм, глубина 500-600мм). Всегда с краю!
- corner — УГЛОВОЙ модуль. ВСЕГДА квадратный: ширина=глубина (600×600, 900×900, 1000×1000). ЭТО ОДИН МОДУЛЬ!

ЧТЕНИЕ ЧЕРТЕЖА — УСЛОВНЫЕ ОБОЗНАЧЕНИЯ:
★ Пунктирные треугольники на дверце = направление открывания. Это НЕ отдельный модуль, это маркер одной дверцы!
★ Горизонтальная линия внутри нижней базы = деление на ящики. ВСЯ база — ОДИН модуль, укажи drawers.count = количеству ящиков.
★ Верхние базы РАЗНОЙ глубины (350мм vs 280мм) — это разные модули. Но если глубина одинаковая — это один ярус.
★ Пеналы: на всю высоту кухни, всегда с краю. Левый — часто под холодильник, правый — для коммуникаций.

ОБЩИЕ ПРАВИЛА (нарушение = брак):
1. Один физический корпус = ОДИН модуль. Две дверцы на одном корпусе = 1 модуль с facades.count=2.
2. Шкаф с несколькими фасадами НЕ дробить на несколько модулей.
3. Размерные линии и выноски — это НЕ модули, игнорируй их.
4. Планки-заполнители (40-80мм) — НЕ модули, игнорируй.
5. Модуль уже 150мм — скорее всего ошибка: проверь, не дверца ли это.
6. Если точный размер не читается — стандартный (низ: 560×820, верх: 320×720, пенал: 560×2100).
7. ВСЕ размеры в мм, целыми числами. Никаких "см" или "м".
8. Угловой модуль — всегда is_corner=true, width=depth. Глубина угла НЕ бывает 320мм.
9. Пеналы считай отдельно — они не входят в группу нижних/верхних баз.
10. Если видишь «×3» или «3 шт» — quantity=3.

ОПРЕДЕЛИ ЗОНУ (строго одно из): Кухня, Гостиная, Спальня, Детская, Прихожая, Ванная, Гардеробная, Кабинет, Балкон, Столовая, Постирочная.

МАТЕРИАЛЫ: если на чертеже указаны декоры (EGGER H1379, H3158, EXTRAVERT и т.п.) — перечисли в materials.

ПРИМЕР 1 — прямая кухня (3 нижних + 2 верхних):
{"zone_type":"Кухня","materials":["EGGER H1379","EMDIWAY Platinum"],"modules":[{"type":"lower_base","width":600,"depth":560,"height":820,"quantity":3,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":1,"is_corner":false},{"type":"upper_base","width":600,"depth":320,"height":720,"quantity":2,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":1,"is_corner":false}],"confidence":"high","notes":""}

ПРИМЕР 2 — угловая кухня (corner 900×900 + 2 нижних + 3 верхних):
{"zone_type":"Кухня","materials":["EGGER H3158","МДФ матовый"],"modules":[{"type":"corner","width":900,"depth":900,"height":820,"quantity":1,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":0,"is_corner":true},{"type":"lower_base","width":600,"depth":560,"height":820,"quantity":2,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":1},"shelves":1,"is_corner":false},{"type":"upper_base","width":600,"depth":320,"height":720,"quantity":3,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":1,"is_corner":false}],"confidence":"high","notes":""}

В ответе верни ТОЛЬКО JSON с полями: zone_type, materials, modules, confidence, notes."""


# Старые промпты оставлены для справки и отладки
# (используются только если UNIFIED_PROMPT по какой-то причине не подходит)


# ================================================================
# ВАЛИДАЦИЯ РАЗМЕРОВ
# ================================================================

# Реалистичные диапазоны размеров мебельных модулей (мм)
DIMENSION_LIMITS = {
    "width":    (250, 2400),   # мин/макс ширина модуля (уже 250мм — фильтрует дверцы-фантомы)
    "depth":    (200, 1200),   # мин/макс глубина
    "height":   (300, 2800),   # мин/макс высота
    "quantity": (1, 30),       # мин/макс количество одинаковых модулей
}

# Допустимые диапазоны высот по типам
TYPE_HEIGHT_LIMITS = {
    "lower_base": (400, 1000),    # напольные: 400-1000мм
    "upper_base": (300, 1200),    # навесные: 300-1200мм
    "penal":      (1500, 2800),   # пеналы: от 1500мм
    "corner":     (400, 1000),    # угловые: как нижние базы
    "column":     (1500, 2800),   # колонны: как пеналы
    "tumbler":    (100, 500),     # тумбы: низкие
}

# Стандартные размеры угловых модулей
VALID_CORNER_SIZES = {600, 900, 1000, 1050, 1100}


def _validate_module(module: "RecognizedModule") -> tuple:
    """
    Проверить реалистичность размеров модуля.
    Возвращает (is_valid: bool, reason: str).
    """
    # Проверка диапазонов
    for dim_name, (lo, hi) in DIMENSION_LIMITS.items():
        if dim_name == "quantity":
            continue  # quantity проверяем отдельно
        val = getattr(module, dim_name, 0)
        if val != 0 and (val < lo or val > hi):
            return False, f"{dim_name}={val} вне [{lo}..{hi}]"

    if module.quantity < 1 or module.quantity > DIMENSION_LIMITS["quantity"][1]:
        return False, f"quantity={module.quantity} вне [1..{DIMENSION_LIMITS['quantity'][1]}]"

    # Проверка высоты по типу модуля
    if module.type in TYPE_HEIGHT_LIMITS:
        lo, hi = TYPE_HEIGHT_LIMITS[module.type]
        if module.height != 0 and (module.height < lo or module.height > hi):
            return False, f"{module.type}: height={module.height} вне [{lo}..{hi}]"

    # Угловой модуль должен быть квадратным
    if module.is_corner or module.type == "corner":
        if module.width != module.depth:
            return False, f"corner не квадратный: {module.width}≠{module.depth}"
        if module.width not in VALID_CORNER_SIZES:
            return False, f"corner нестандартный размер: {module.width}мм"

    # Нулевые размеры — явная ошибка
    if module.width <= 0 or module.depth <= 0 or module.height <= 0:
        return False, f"нулевые размеры: {module.width}×{module.depth}×{module.height}"

    return True, ""


# ================================================================
# ОСНОВНОЙ КЛАСС
# ================================================================

def _merge_adjacent_modules(modules: List["RecognizedModule"]) -> List["RecognizedModule"]:
    """
    Слить соседние модули одного типа/высоты/глубины в общий корпус с несколькими фасадами.

    Эвристика: если два модуля имеют одинаковый type, height, depth
    и их суммарная ширина ≤ 1200мм — это один корпус с facades.count > 1.
    Планки-заполнители между ними (если есть) игнорируются.

    Также обрабатывает случай horizontal split: если у двух lower_base
    одинаковая ширина и глубина, но разная высота, и меньший сверху —
    это одна база с ящиками (drawers).
    """
    if len(modules) <= 1:
        return list(modules)

    # Сортируем по типу, затем по высоте (для группировки)
    sorted_mods = sorted(modules, key=lambda m: (m.type, m.height, m.depth))

    merged = []
    i = 0
    while i < len(sorted_mods):
        current = sorted_mods[i]
        group = [current]

        # Ищем соседей того же типа, высоты и глубины
        j = i + 1
        while j < len(sorted_mods):
            candidate = sorted_mods[j]
            if (candidate.type == current.type
                and candidate.height == current.height
                and candidate.depth == current.depth):
                total_width = sum(m.width for m in group) + candidate.width
                if total_width <= 1200:
                    group.append(candidate)
                    j += 1
                else:
                    break
            else:
                break

        if len(group) == 1:
            merged.append(current)
        else:
            # Сливаем в один модуль
            total_w = sum(m.width for m in group)
            total_qty = sum(max(m.quantity, 1) for m in group)
            total_facades = sum(
                m.facades.get("count", 1) if m.facades else 1
                for m in group
            )
            total_drawers = sum(
                m.drawers.get("count", 0) if m.drawers else 0
                for m in group
            )
            max_shelves = max(m.shelves for m in group)

            merged_mod = RecognizedModule(
                type=current.type,
                width=total_w,
                depth=current.depth,
                height=current.height,
                quantity=1,  # один корпус
                has_glass=any(m.has_glass for m in group),
                facades={"count": total_facades, "type": "doors"},
                drawers={"count": total_drawers} if total_drawers > 0 else None,
                shelves=max_shelves,
                is_corner=False,
                bbox=current.bbox,
            )
            merged.append(merged_mod)
            logger.info(
                f"Merged {len(group)} adjacent modules "
                f"({current.type} {current.height}mm) → "
                f"{total_w}mm, facades={total_facades}"
            )

        i = j

    return merged


class GeminiImageAnalyzer:
    """
    Анализатор изображений чертежей через Vision LLM.

    Провайдеры (по порядку):
    1. Z.ai GLM-5V-Turbo — основной, быстрый, дешёвый
    2. RouterAI.ru GLM-4.6V — быстрый fallback (через ru-агрегатор)
    3. RouterAI.ru Qwen3-VL-32B — ансамбль / кросс-валидация (SpatialBench SOTA)
    4. RouterAI.ru Gemini 2.5 Flash Lite — последний рубеж
    """

    FALLBACK_CHAIN = [
        # Быстрый fallback — GLM-4.6V через RouterAI.ru (30₽/1M вход)
        ("z-ai/glm-4.6v", "routerai"),
        # Qwen3-VL-32B — лучший spatial reasoning, открытая модель (10₽/1M вход)
        ("qwen/qwen3-vl-32b-instruct", "routerai"),
        # Gemini 2.5 Flash Lite — последний рубеж (10₽/1M вход)
        ("google/gemini-2.5-flash-lite", "routerai"),
    ]

    def __init__(self):
        """Инициализация HTTP клиента."""
        self.zai_key = settings.zai_api_key
        self.openrouter_key = settings.openrouter_api_key
        self.routerai_key = settings.routerai_api_key
        self.routerai_url = settings.routerai_api_url
        self.primary_model = settings.vision_model
        self.api_url = settings.vision_api_url

        self.client: Optional[httpx.AsyncClient] = None

        providers = ["Z.ai"]
        if self.routerai_key:
            providers.append("RouterAI.ru")
        logger.info(
            f"Vision Analyzer: primary={self.primary_model}, "
            f"providers={providers}"
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
        1. Основная модель → если high confidence и >0 модулей → готово
        2. Если medium/low confidence → запрос второй модели (ансамбль)
        3. Сравнение: высокая сходимость → confidence ↑
        4. Если основная не дала модулей → цепочка fallback
        """
        # Строим цепочку: основная модель + fallback
        models_to_try = [(self.primary_model, self._detect_provider(self.primary_model))]
        for model, provider in self.FALLBACK_CHAIN:
            if model != self.primary_model:
                models_to_try.append((model, provider))

        last_error = None

        # ── Попытка 1: основная модель ──
        try:
            logger.info(f"🎯 Основная модель: {self.primary_model}")
            result1 = await self._try_analyze(image_path, self.primary_model,
                                              self._detect_provider(self.primary_model))
            result1.model_used = self.primary_model

            if result1.modules:
                logger.info(
                    f"✅ Primary: {len(result1.modules)} modules, "
                    f"confidence={result1.confidence}"
                )

                # Высокая/средняя уверенность → сразу возвращаем (без ансамбля)
                if result1.confidence in ("high", "medium") and len(result1.modules) >= 2:
                    return result1

                # Только низкая уверенность → АНСАМБЛЬ (вторая модель)
                if len(models_to_try) > 1:
                    logger.info("🔄 Ансамбль: запрос второй модели для кросс-валидации...")
                    fallback_model, fallback_provider = models_to_try[1]
                    try:
                        result2 = await self._try_analyze(image_path, fallback_model, fallback_provider)
                        result2.model_used = fallback_model

                        if result2.modules:
                            result1 = self._ensemble_merge(result1, result2)
                            logger.info(
                                f"🎯 Ансамбль: confidence={result1.confidence}, "
                                f"modules={len(result1.modules)}"
                            )
                    except Exception as e:
                        logger.warning(f"Ансамбль не удался: {e}")

                return result1
            else:
                logger.warning(f"Основная модель вернула пустой результат")

        except Exception as e:
            last_error = e
            logger.error(f"Ошибка основной модели: {type(e).__name__}: {e}")

        # ── Попытки 2+: fallback-цепочка (если основная не дала модулей) ──
        for attempt, (model, provider) in enumerate(models_to_try[1:max_retries], start=2):
            try:
                logger.info(f"Попытка #{attempt}: {model} ({provider})")
                result = await self._try_analyze(image_path, model, provider)

                if result.modules and len(result.modules) > 0:
                    result.model_used = model
                    logger.info(
                        f"✅ Fallback success: model={model}, modules={len(result.modules)}, "
                        f"confidence={result.confidence}"
                    )
                    return result
                else:
                    logger.warning(f"Модель {model} вернула пустой результат")

            except Exception as e:
                last_error = e
                logger.error(f"Ошибка {model}: {type(e).__name__}: {e}")
                if attempt < min(len(models_to_try), max_retries):
                    delay = 2 * (attempt - 1)
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

        # 1. Загружаем и улучшаем изображение
        image = Image.open(image_path)
        logger.info(f"Изображение: {image.size}, mode={image.mode}")

        # 1a. Повышаем контраст — критические размерные линии и цифры
        from PIL import ImageEnhance, ImageFilter
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(1.3)  # +30% контраст

        # 1b. Лёгкое повышение резкости — границы модулей и текст
        image = image.filter(ImageFilter.SHARPEN)

        # 1c. Уменьшаем если больше 1536px (быстрее передача, качество достаточное)
        max_size = 1536
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
            "temperature": 0.0,              # детерминированный вывод для чертежей
            "max_tokens": 8000,
            "response_format": {"type": "json_object"},  # гарантирует валидный JSON на выходе
        }

        # GLM-модели: reasoning_effort только для прямого Z.ai (не через RouterAI)
        if "glm" in model.lower() and provider == "zai":
            body["reasoning_effort"] = "medium"

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
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # -----------------------------------------------------------

    def _detect_provider(self, model: str) -> str:
        """Определить провайдера по имени модели."""
        if "glm" in model.lower() and "z-ai" not in model.lower():
            return "zai"      # GLM без префикса → Z.ai
        if "z-ai" in model.lower():
            return "routerai"  # z-ai/glm-* → RouterAI.ru (агрегатор)
        if "qwen" in model.lower() or "google" in model.lower() or "gemini" in model.lower():
            return "routerai"  # Qwen, Gemini → RouterAI.ru
        return "zai"  # по умолчанию

    def _get_api_key(self, provider: str) -> str:
        """Получить API ключ для провайдера."""
        if provider == "zai":
            return self.zai_key or ""
        elif provider == "routerai":
            return self.routerai_key or self.zai_key or ""
        elif provider == "openrouter":
            return self.openrouter_key or self.routerai_key or ""
        return self.zai_key or self.routerai_key or ""

    def _get_api_url(self, provider: str, model: str) -> str:
        """Получить URL эндпоинта."""
        if provider == "zai":
            return "https://api.z.ai/api/paas/v4/chat/completions"
        elif provider == "routerai":
            return self.routerai_url  # https://routerai.ru/api/v1/chat/completions
        elif provider == "openrouter":
            return "https://openrouter.ai/api/v1/chat/completions"
        return self.api_url

    def _select_prompt(self, model: str) -> str:
        """Выбрать промпт. Все модели используют унифицированный промпт."""
        return UNIFIED_PROMPT

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

                # Валидация размеров — отсеиваем мусорные модули
                is_valid, reason = _validate_module(module)
                if not is_valid:
                    logger.warning(f"⚠️ Пропущен модуль: {reason}, data={module_data}")
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
        """Постобработка: убираем дубликаты, фиксим ошибки классификации, сливаем раздробленные модули."""
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

        # ── СЛИЯНИЕ РАЗДРОБЛЕННЫХ МОДУЛЕЙ ──
        # Если два соседних модуля одного типа, высоты и глубины
        # и их суммарная ширина ≤ 1200мм → это ОДИН корпус с несколькими фасадами
        merged_regular = _merge_adjacent_modules(regular)
        processed.extend(merged_regular)

        logger.info(f"Post-process: {len(result.modules)} → {len(processed)}")

        return RecognitionResult(
            modules=processed,
            confidence=result.confidence,
            notes=result.notes,
            zone_type=result.zone_type,
            materials_mentioned=result.materials_mentioned,
        )

    def _ensemble_merge(
        self,
        result1: RecognitionResult,
        result2: RecognitionResult,
    ) -> RecognitionResult:
        """
        Сравнить результаты двух моделей и объединить.

        Логика:
        - Сравниваем модули по типу и близости размеров (width ±50мм, depth ±20мм)
        - Совпадение ≥70% → confidence "high"
        - Совпадение 40-70% → confidence "medium"
        - Совпадение <40% → confidence "low", добавляем предупреждение
        - Модули из result2, которых нет в result1 — добавляем с пометкой
        """
        mods1 = result1.modules
        mods2 = result2.modules

        if not mods2:
            return result1

        # Ищем совпадения
        matched_2 = set()  # индексы модулей из result2, которые совпали
        new_from_2 = []    # модули из result2, которых нет в result1

        for i2, m2 in enumerate(mods2):
            found = False
            for m1 in mods1:
                if (m1.type == m2.type
                    and abs(m1.width - m2.width) <= 50
                    and abs(m1.depth - m2.depth) <= 20):
                    found = True
                    break
            if found:
                matched_2.add(i2)
            else:
                new_from_2.append(m2)

        # Считаем overlap (относительно result1)
        match_count = len(matched_2)
        overlap = match_count / max(len(mods1), 1)

        # Определяем confidence и заметки
        if overlap >= 0.7:
            new_confidence = "high"
            ensemble_note = f" [Ансамбль: сходимость {overlap:.0%} — высокая]"
        elif overlap >= 0.4:
            new_confidence = "medium"
            ensemble_note = f" [Ансамбль: сходимость {overlap:.0%} — средняя]"
        else:
            new_confidence = "low"
            ensemble_note = f" [Ансамбль: модели расходятся ({overlap:.0%}) — нужна проверка!]"

        # Добавляем модули из второй модели, которых нет в первой
        if new_from_2:
            logger.info(
                f"Ансамбль: +{len(new_from_2)} модулей из второй модели: "
                f"{[f'{m.type} {m.width}×{m.depth}×{m.height}' for m in new_from_2]}"
            )
            result1.modules.extend(new_from_2)
            ensemble_note += f" +{len(new_from_2)} доп. модулей от {result2.model_used}"

        result1.confidence = new_confidence
        result1.notes = (result1.notes or "") + ensemble_note

        # Материалы: объединяем без дубликатов
        combined_materials = list(dict.fromkeys(
            result1.materials_mentioned + result2.materials_mentioned
        ))
        result1.materials_mentioned = combined_materials

        # Zone_type: если у result1 нет — берём из result2
        if not result1.zone_type and result2.zone_type:
            result1.zone_type = result2.zone_type

        return result1


# ================================================================
# ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР
# ================================================================

_analyzer: Optional[GeminiImageAnalyzer] = None


def get_analyzer() -> GeminiImageAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = GeminiImageAnalyzer()
    return _analyzer
