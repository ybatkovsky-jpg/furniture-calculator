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
# ПРОМПТЫ
# ================================================================

# GLM-4.6V: используем его суперсилу — object detection с bounding boxes
GLM46V_PROMPT = """Ты — парсер мебельных чертежей. Твоя единственная задача — выдать JSON с модулями. НЕ рассуждай, НЕ объясняй, НЕ пиши текст.

ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА:
```json
{
  "zone_type": "kitchen",
  "materials": ["EGGER H1379", "EMDIWAY Platinum"],
  "modules": [
    {
      "type": "lower_base",
      "width": 600,
      "depth": 560,
      "height": 820,
      "quantity": 3,
      "has_glass": false,
      "facades": {"count": 1, "type": "doors"},
      "drawers": {"count": 0},
      "shelves": 0,
      "is_corner": false
    }
  ],
  "confidence": "high",
  "notes": ""
}
```

ТИПЫ МОДУЛЕЙ:
- lower_base: напольный, высота ~820-850мм, глубина ~560мм
- upper_base: навесной, высота ~700-920мм, глубина ~320мм  
- penal: высокий от пола, ~2000-2400мм
- corner: УГЛОВОЙ квадратный (600×600, 900×900). ОДИН модуль, НЕ два!

ПРАВИЛА:
- Размеры только в мм (целые числа)
- ВСЕ ключи JSON в двойных кавычках
- НИКАКОГО текста до или после JSON
- ОТВЕТ НАЧИНАЕТСЯ С ```json И ЗАКАНЧИВАЕТСЯ ```"""


# Qwen3-VL: используем пространственное мышление (SpatialBench 13.5)
QWEN3_VL_PROMPT = """Ты — ведущий конструктор-технолог премиальной мебельной фабрики.
Твоя задача — проанализировать чертёж/фото и извлечь ВСЕ модули мебели в JSON.

ВАЖНЫЕ ПРАВИЛА:
1. Ищи ПРЯМОУГОЛЬНИКИ с размерами (ширина × глубина × высота) или (Ш × Г × В)
2. Определяй ТИП модуля по его расположению и размерам:
   - lower_base (нижняя база): стоит на полу, высота ~820-850мм
   - upper_base (верхняя база): навесной, высота ~700-920мм
   - penal (пенал): высокий от пола, высота ~2000-2400мм
   - corner (угловой): расположен в углу, часто квадратный (600×600, 900×900)

3. УГЛОВЫЕ МОДУЛИ: если видишь квадратный модуль в углу (600×600, 900×900) — это ОДИН corner.

4. СТАНДАРТНЫЕ РАЗМЕРЫ (используй если не указано):
   - Глубина нижних баз: 560мм, верхних: 320мм
   - Высота нижних: 820мм, верхних: 720мм, пеналов: 2100мм

5. ОПРЕДЕЛИ ЗОНУ: kitchen / wardrobe / bathroom / hallway

ФОРМАТ ОТВЕТА — ТОЛЬКО JSON, БЕЗ markdown:
{
  "zone_type": "kitchen",
  "materials": ["EGGER H1379"],
  "modules": [
    {
      "type": "lower_base",
      "width": 600, "depth": 560, "height": 820,
      "quantity": 1, "has_glass": false,
      "facades": {"count": 1, "type": "doors"},
      "drawers": {"count": 0}, "shelves": 0,
      "is_corner": false
    }
  ],
  "confidence": "high",
  "notes": ""
}"""


# Gemini: запасной промпт
GEMINI_PROMPT = """Ты — эксперт по распознаванию мебельных чертежей кухонной мебели. ВНИМАТЕЛЬНО анализируй изображение и выдели все модули.

ПРАВИЛА:
- Ищи ПРЯМОУГОЛЬНЫЕ модули с размерами
- УГЛОВЫЕ модули: КВАДРАТНЫЕ (600x600, 900x900) в УГЛУ — это ОДИН модуль, не два!
- Типы: lower_base (низ, ~820мм), upper_base (верх, ~720мм), penal (~2100мм), corner (угол)
- Стандартные размеры: глубина нижних 560мм, верхних 320мм

Формат ответа — ТОЛЬКО JSON:
{
  "modules": [
    {
      "type": "lower_base|upper_base|penal|corner",
      "width": 600, "depth": 560, "height": 820,
      "quantity": 1, "has_glass": false,
      "facades": {"count": 2, "type": "doors"},
      "drawers": {"count": 0}, "shelves": 1
    }
  ],
  "confidence": "high|medium|low",
  "notes": ""
}"""


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
        Пробует модели по цепочке: GLM-4.6V → Qwen3-VL → Gemini.
        """
        # Строим цепочку: основная модель + fallback
        models_to_try = [(self.primary_model, self._detect_provider(self.primary_model))]
        for model, provider in self.FALLBACK_CHAIN:
            if model != self.primary_model:
                models_to_try.append((model, provider))

        last_error = None
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
                    return result
                else:
                    logger.warning(f"Модель {model} вернула пустой результат")

            except Exception as e:
                last_error = e
                logger.error(
                    f"Ошибка {model}: {type(e).__name__}: {e}",
                    exc_info=(attempt == 0)  # полный traceback только для первой ошибки
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
