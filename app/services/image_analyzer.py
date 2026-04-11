"""
Сервис для распознавания чертежей мебели через Google Gemini Flash API.

Использует модель google/gemini-2.5-flash-lite-preview-09-2025 через OpenAI-совместимый API
с JSON-ответом для автоматического парсинга модулей мебели из фотографий чертежей.
"""

import json
import logging
import base64
import io
import time
from typing import Dict, List, Optional, Any
from pathlib import Path
from dataclasses import dataclass

import httpx
from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RecognizedModule:
    """Распознанный модуль мебели."""
    type: str  # "lower_base", "upper_base", "penal", "column", "tumbler", "corner"
    width: int  # ширина в мм
    depth: int  # глубина в мм
    height: int  # высота в мм
    quantity: int = 1
    has_glass: bool = False
    facades: Dict[str, Any] = None  # наполнение фасадов
    drawers: Dict[str, Any] = None  # ящики
    shelves: int = 0  # полки
    is_corner: bool = False  # является ли угловым модулем


@dataclass
class RecognitionResult:
    """Результат распознавания чертежа."""
    modules: List[RecognizedModule]
    confidence: str  # "high", "medium", "low"
    notes: Optional[str] = None


class GeminiImageAnalyzer:
    """Анализатор изображений чертежей через Gemini Flash."""

    def __init__(self):
        """Инициализация HTTP клиента для Gemini API."""
        self.api_key = settings.gemini_api_key
        self.base_url = "https://routerai.ru/api/v1"
        self.client = httpx.Client(timeout=60.0)

    def analyze_drawing(self, image_path: str | Path, max_retries: int = 3) -> RecognitionResult:
        """
        Распознать модули мебели на чертеже с повторными попытками.

        Args:
            image_path: Путь к изображению чертежа
            max_retries: Максимальное количество попыток

        Returns:
            RecognitionResult с распознанными модулями
        """
        for attempt in range(max_retries):
            try:
                logger.info(f"Попытка распознавания #{attempt + 1}/{max_retries}")

                # Загружаем изображение и конвертируем в base64
                image = Image.open(image_path)
                logger.info(f"Image loaded: {image.size}, mode: {image.mode}")

                # Проверяем размер и уменьшаем если нужно
                max_size = 1024
                if image.width > max_size or image.height > max_size:
                    # Вычисляем новый размер, сохраняя пропорции
                    ratio = min(max_size / image.width, max_size / image.height)
                    new_size = (int(image.width * ratio), int(image.height * ratio))
                    image = image.resize(new_size, Image.Resampling.LANCZOS)
                    logger.info(f"Image resized to: {image.size}")

                # Конвертируем изображение в base64
                buffer = io.BytesIO()
                image.save(buffer, format='JPEG', quality=85)
                image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                logger.info(f"Image encoded to base64, size: {len(image_base64)} chars")

                # Создаем промпт для Gemini
                prompt = self._create_analysis_prompt()

                # Создаем сообщение с изображением
                messages = [
                    {
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
                    }
                ]

                # Отправляем запрос через httpx
                response = self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "google/gemini-2.5-flash-lite-preview-09-2025",
                        "messages": messages,
                        "temperature": 0.1,
                        "max_tokens": 2000
                    }
                )

                response.raise_for_status()
                response_data = response.json()

                # Получаем ответ
                response_text = response_data["choices"][0]["message"]["content"]
                logger.info(f"Gemini response (attempt {attempt + 1}): {response_text[:500]}...")

                # Очищаем ответ от возможных markdown блоков
                cleaned_text = response_text.strip()
                if cleaned_text.startswith('```json'):
                    cleaned_text = cleaned_text[7:]
                if cleaned_text.startswith('```'):
                    cleaned_text = cleaned_text[3:]
                if cleaned_text.endswith('```'):
                    cleaned_text = cleaned_text[:-3]
                cleaned_text = cleaned_text.strip()

                # Ищем JSON в ответе (может быть обёрнут в текст)
                json_start = cleaned_text.find('{')
                json_end = cleaned_text.rfind('}') + 1

                if json_start != -1 and json_end > json_start:
                    json_content = cleaned_text[json_start:json_end]
                    logger.info(f"Extracted JSON: {json_content[:200]}...")
                    try:
                        result_data = json.loads(json_content)
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error: {e}, content: {json_content}")
                        if attempt < max_retries - 1:
                            time.sleep(2)
                            continue
                        raise
                else:
                    # Если JSON не найден, пробуем распарсить весь ответ
                    logger.warning("JSON not found in response, trying to parse whole response")
                    try:
                        result_data = json.loads(cleaned_text)
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse response as JSON: {e}")
                        if attempt < max_retries - 1:
                            time.sleep(2)
                            continue
                        raise

                # Преобразуем в наши объекты
                result = self._parse_result(result_data)

                # Постобработка результатов - очистка от возможных дубликатов
                result = self._postprocess_results(result)

                # Проверяем, что результат не пустой
                if result.modules and len(result.modules) > 0:
                    logger.info(f"Успешное распознавание с {attempt + 1} попытки")
                    return result
                else:
                    logger.warning(f"Пустой результат распознавания на попытке {attempt + 1}")
                    if attempt < max_retries - 1:
                        time.sleep(2)  # Задержка перед следующей попыткой
                        continue

                return result

            except Exception as e:
                logger.error(f"Ошибка при распознавании чертежа (попытка {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(3)  # Задержка перед следующей попыткой
                    continue

                # Возвращаем результат с низкой уверенностью после всех попыток
                return RecognitionResult(
                    modules=[],
                    confidence="low",
                    notes=f"Ошибка распознавания после {max_retries} попыток: {str(e)}"
                )

    def _create_analysis_prompt(self) -> str:
        """Создать системный промпт для Gemini."""
        prompt = "Ты — эксперт по распознаванию мебельных чертежей кухонной мебели. ВНИМАТЕЛЬНО анализируй изображение и выдели все модули.\n\n"
        prompt += "ВАЖНЫЕ ПРАВИЛА РАСПОЗНАВАНИЯ:\n"
        prompt += "- Ищи ПРЯМОУГОЛЬНЫЕ модули с указанными размерами\n"
        prompt += "- УГЛОВЫЕ модули: КВАДРАТНЫЕ модули (600x600, 900x900) в УГЛУ помещения\n"
        prompt += "- НЕ РАЗБИВАЙ угловые модули на два отдельных прямоугольника!\n"
        prompt += "- Если видишь квадратный модуль в углу - это УГЛОВОЙ модуль, не два модуля\n"
        prompt += "- Угловые модули могут иметь две стороны фасада под 90 градусов\n\n"
        prompt += "Мебельная терминология:\n"
        prompt += "- Нижняя база (lower_base): напольные шкафы под столешницей, высота ~820мм\n"
        prompt += "- Верхняя база (upper_base): навесные шкафы над столешницей, высота ~720мм\n"
        prompt += "- Пенал (penal): высокие шкафы от пола до потолка, высота ~2100мм\n"
        prompt += "- УГЛОВОЙ модуль (corner): квадратный модуль для угла, размеры 600x600, 900x900\n\n"
        prompt += "Стандартные размеры:\n"
        prompt += "- Обычные модули: ширина 300-1200мм, глубина нижних 560мм, верхних 320мм\n"
        prompt += "- УГЛОВЫЕ модули: могут быть 600x600мм, 900x900мм (квадратные)\n"
        prompt += "- Высота нижних: 820мм, верхних: 720мм\n\n"
        prompt += "ПРИЗНАКИ УГЛОВЫХ МОДУЛЕЙ:\n"
        prompt += "- Расположены в углу помещения\n"
        prompt += "- Могут иметь две стороны фасада под 90 градусов\n"
        prompt += "- Часто имеют размеры типа 600x600 или 900x900\n"
        prompt += "- Могут быть соединены с другими модулями под углом\n\n"
        prompt += "Формат ответа (ТОЛЬКО JSON):\n"
        prompt += "{\n"
        prompt += '  "modules": [\n'
        prompt += "    {\n"
        prompt += '      "type": "lower_base|upper_base|penal|corner",\n'
        prompt += '      "width": 600,\n'
        prompt += '      "depth": 600,\n'
        prompt += '      "height": 720,\n'
        prompt += '      "quantity": 1,\n'
        prompt += '      "has_glass": false,\n'
        prompt += '      "facades": {"count": 2, "type": "doors"},\n'
        prompt += '      "drawers": {"count": 0, "brand": null},\n'
        prompt += '      "shelves": 1\n'
        prompt += "    }\n"
        prompt += "  ],\n"
        prompt += '  "confidence": "high|medium|low",\n'
        prompt += '  "notes": "укажите если есть угловые модули"\n'
        prompt += "}\n\n"
        prompt += "Правила:\n"
        prompt += "- ОТВЕТЬ ТОЛЬКО JSON, БЕЗ ДОПОЛНИТЕЛЬНОГО ТЕКСТА\n"
        prompt += "- КВАДРАТНЫЕ модули (600x600) - это УГЛОВЫЕ, не два отдельных\n"
        prompt += "- Размеры в мм, quantity по умолчанию 1"
        return prompt

    def _parse_result(self, data: Dict[str, Any]) -> RecognitionResult:
        """Преобразовать JSON-ответ Gemini в RecognitionResult."""
        modules = []

        for module_data in data.get("modules", []):
            try:
                is_corner = module_data["type"] == "corner" or (
                    module_data["type"] in ["lower_base", "upper_base"] and
                    module_data["width"] == module_data["depth"] and
                    module_data["width"] in [600, 900, 1000]
                )

                module = RecognizedModule(
                    type=module_data["type"],
                    width=int(module_data["width"]),
                    depth=int(module_data["depth"]),
                    height=int(module_data["height"]),
                    quantity=module_data.get("quantity", 1),
                    has_glass=module_data.get("has_glass", False),
                    facades=module_data.get("facades"),
                    drawers=module_data.get("drawers"),
                    shelves=module_data.get("shelves", 0),
                    is_corner=is_corner
                )
                modules.append(module)
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"Ошибка парсинга модуля: {e}, данные: {module_data}")

        confidence = data.get("confidence", "low")
        notes = data.get("notes")

        return RecognitionResult(
            modules=modules,
            confidence=confidence,
            notes=notes
        )

    def _postprocess_results(self, result: RecognitionResult) -> RecognitionResult:
        """Постобработка результатов распознавания для очистки от дубликатов."""
        if not result.modules:
            return result

        logger.info(f"Исходные модули: {[f'{m.type} {m.width}x{m.depth}x{m.height}' for m in result.modules]}")
        processed_modules = []

        # Собираем модули
        valid_corners = []
        regular_modules = []

        for module in result.modules:
            if module.type == "corner":
                # Corner модули должны быть квадратными
                if module.width == module.depth and module.width in [600, 900, 1000]:
                    valid_corners.append(module)
                else:
                    # Неквадратный corner - переводим в обычный модуль
                    module.type = "upper_base"  # Предполагаем, что corner был upper_base
                    module.is_corner = False
                    regular_modules.append(module)
                    logger.warning(f"Неквадратный corner модуль {module.width}x{module.depth} переведен в upper_base")
            else:
                regular_modules.append(module)

        # Оставляем только уникальные corner модули (по width и depth, игнорируя height)
        unique_corners = {}
        for corner in valid_corners:
            key = f"{corner.width}_{corner.depth}"  # Игнорируем height для corner модулей
            if key not in unique_corners:
                unique_corners[key] = corner
            else:
                logger.warning(f"Найден дубликат corner модуля {key}: заменен на новый")

        processed_modules.extend(unique_corners.values())

        duplicates_removed = len(valid_corners) - len(unique_corners)
        if duplicates_removed > 0:
            logger.warning(f"Удалены дубликаты corner модулей: {duplicates_removed} шт")

        logger.info(f"Corner модули: {len(unique_corners)} уникальных")
        logger.info(f"Regular модули: {len(regular_modules)} шт")

        # Обрабатываем обычные модули
        module_groups = {}
        for module in regular_modules:
            key = f"{module.type}_{module.width}_{module.depth}_{module.height}"
            if key not in module_groups:
                module_groups[key] = []
            module_groups[key].append(module)

        logger.info(f"Группы модулей: {list(module_groups.keys())}")

        # Анализируем группы обычных модулей
        for key, modules in module_groups.items():
            if len(modules) == 1:
                processed_modules.append(modules[0])
            else:
                module_type, dimensions = key.split('_', 1)

                if module_type in ["upper_base", "lower_base"]:
                    width, depth, height = map(int, dimensions.split('_'))

                    # Проверяем на возможные ошибки интерпретации угловых модулей
                    # Если есть модули 600x320 и есть corner модуль 600x600
                    if width == 600 and depth == 320:
                        # Проверяем наличие corner модулей 600x600
                        has_corner_600 = any(c.width == 600 and c.depth == 600 for c in unique_corners.values())
                        if has_corner_600:
                            # Есть corner 600x600 - модули 600x320 могут быть ошибкой интерпретации
                            # Не добавляем их вообще, или оставляем максимум 1
                            total_quantity = sum(m.quantity for m in modules)
                            if total_quantity <= 1:
                                # Оставляем только если всего 1
                                combined_module = modules[0].__class__(
                                    type=modules[0].type,
                                    width=modules[0].width,
                                    depth=modules[0].depth,
                                    height=modules[0].height,
                                    quantity=1,
                                    has_glass=modules[0].has_glass,
                                    facades=modules[0].facades,
                                    drawers=modules[0].drawers,
                                    shelves=modules[0].shelves,
                                    is_corner=modules[0].is_corner
                                )
                                processed_modules.append(combined_module)
                                logger.warning(f"Corner 600x600 найден: оставлен 1 модуль 600x320 (возможно, это часть углового)")
                            else:
                                # Если больше 1, возможно, это ошибка - не добавляем
                                logger.warning(f"Corner 600x600 найден: пропущены {total_quantity} модулей 600x320 (вероятно, ошибка интерпретации)")
                        else:
                            # Оставляем все, но объединяем в один модуль с суммарным quantity
                            total_quantity = sum(m.quantity for m in modules)
                            combined_module = modules[0].__class__(
                                type=modules[0].type,
                                width=modules[0].width,
                                depth=modules[0].depth,
                                height=modules[0].height,
                                quantity=total_quantity,
                                has_glass=modules[0].has_glass,
                                facades=modules[0].facades,
                                drawers=modules[0].drawers,
                                shelves=modules[0].shelves,
                                is_corner=modules[0].is_corner
                            )
                            processed_modules.append(combined_module)
                    elif len(modules) <= 3:
                        # Оставляем все, но объединяем в один модуль с суммарным quantity
                        total_quantity = sum(m.quantity for m in modules)
                        combined_module = modules[0].__class__(
                            type=modules[0].type,
                            width=modules[0].width,
                            depth=modules[0].depth,
                            height=modules[0].height,
                            quantity=total_quantity,
                            has_glass=modules[0].has_glass,
                            facades=modules[0].facades,
                            drawers=modules[0].drawers,
                            shelves=modules[0].shelves,
                            is_corner=modules[0].is_corner
                        )
                        processed_modules.append(combined_module)
                    else:
                        # Слишком много одинаковых модулей - оставляем только один
                        processed_modules.append(modules[0])
                        logger.warning(f"Слишком много одинаковых модулей {key}: оставлен 1 из {len(modules)}")
                else:
                    # Для других типов объединяем quantity
                    total_quantity = sum(m.quantity for m in modules)
                    combined_module = modules[0].__class__(
                        type=modules[0].type,
                        width=modules[0].width,
                        depth=modules[0].depth,
                        height=modules[0].height,
                        quantity=total_quantity,
                        has_glass=modules[0].has_glass,
                        facades=modules[0].facades,
                        drawers=modules[0].drawers,
                        shelves=modules[0].shelves,
                        is_corner=modules[0].is_corner
                    )
                    processed_modules.append(combined_module)

        logger.info(f"Постобработка: {len(result.modules)} -> {len(processed_modules)} модулей")
        logger.info(f"Итоговые модули: {[f'{m.type} {m.width}x{m.depth}x{m.height}' for m in processed_modules]}")

        return RecognitionResult(
            modules=processed_modules,
            confidence=result.confidence,
            notes=result.notes
        )