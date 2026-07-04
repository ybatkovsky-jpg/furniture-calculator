# ROADMAP: Повышение точности распознавания и корректности заполнения

> Версия: 1.0  
> Дата: 2026-07-03  
> Ветка: `commit-changes`  
> Цель: снизить количество ручных правок оператора с ~30% до <5%

---

## Сводка по фазам

| Фаза | Дней | Суть | ROI |
|------|------|------|-----|
| 🔴 Фаза 1 — Быстрые победы | 1–2 | JSON-format, валидация, умные страницы, отчёт о пропусках | ⭐⭐⭐⭐⭐ |
| 🟡 Фаза 2 — Качество распознавания | 2–3 | Ансамбль моделей, предобработка, улучшенные промпты | ⭐⭐⭐⭐ |
| 🟡 Фаза 3 — Точность расчёта | 2–3 | Смежные стенки, рефакторинг детекции, настраиваемые эвристики | ⭐⭐⭐ |
| 🟢 Фаза 4 — Надёжность заполнения | 1–2 | Нечёткий поиск, валидация шаблона, внешний конфиг ROW_MAPPING | ⭐⭐⭐ |
| 🔵 Фаза 5 — Кросс-валидация | 2–3 | OCR↔Vision, quality gates, интерфейс проверки оператором | ⭐⭐⭐⭐ |
| **Итого** | **8–13** | | |

---

## 🔴 ФАЗА 1 — Быстрые победы (1–2 дня)

> **Цель:** 2 строки кода → −80% ошибок парсинга JSON. Минимум усилий, максимум эффекта.

### 1.1 `response_format: json_object` в Vision API

**Файл:** `app/services/image_analyzer.py`  
**Строки:** ~329–335 (метод `_try_analyze`, блок `body`)

**Проблема:** Модели возвращают JSON внутри markdown-блоков, reasoning-текста или с комментариями. `_extract_json()` делает сложный парсинг, но всё равно ломается на ~15% ответов.

**Решение:** Добавить `response_format` в тело запроса — модель гарантированно вернёт валидный JSON.

```python
# БЫЛО (строка 329-335):
body = {
    "model": model,
    "messages": messages,
    "temperature": 0.1,
    "max_tokens": 8000,
    "reasoning_effort": "medium",
}

# СТАЛО:
body = {
    "model": model,
    "messages": messages,
    "temperature": 0.1,
    "max_tokens": 8000,
    "reasoning_effort": "medium",
    "response_format": {"type": "json_object"},  # ← ДОБАВИТЬ
}
```

**Важно:** Не все модели поддерживают `json_object` (GLM-4.6V — да, Qwen3 — да, Gemini — да). Для GLM-OCR (другой эндпоинт) не трогаем.

**Ожидаемый эффект:** ошибки `_extract_json` падают с ~15% до <2%.

---

### 1.2 Валидация размеров модулей после распознавания

**Файл:** `app/services/image_analyzer.py`  
**Строки:** ~479–528 (метод `_parse_result`)

**Проблема:** Модель иногда выдаёт нереалистичные размеры: `width: 6000`, `depth: 0`, `height: 100`. Такие модули попадают в `quantity_calc` и порождают бессмысленные количества.

**Решение:** Фильтровать модули с размерами вне реалистичного диапазона.

```python
# В начало image_analyzer.py, после импортов:
# Реалистичные диапазоны размеров мебельных модулей (мм)
DIMENSION_LIMITS = {
    "width":  (150, 2400),   # мин/макс ширина
    "depth":  (200, 1200),   # мин/макс глубина
    "height": (300, 2800),   # мин/макс высота
    "quantity": (1, 30),     # мин/макс количество
}

# В _parse_result, в цикле for module_data in data.get("modules", []):
# после строки `module = RecognizedModule(...)`, перед `modules.append(module)`:

def _validate_module(module: RecognizedModule) -> Tuple[bool, str]:
    """Проверить реалистичность размеров модуля. Возвращает (валиден, причина)."""
    if module.width < DIMENSION_LIMITS["width"][0] or module.width > DIMENSION_LIMITS["width"][1]:
        return False, f"width={module.width} вне [{DIMENSION_LIMITS['width'][0]}..{DIMENSION_LIMITS['width'][1]}]"
    if module.depth < DIMENSION_LIMITS["depth"][0] or module.depth > DIMENSION_LIMITS["depth"][1]:
        return False, f"depth={module.depth} вне [{DIMENSION_LIMITS['depth'][0]}..{DIMENSION_LIMITS['depth'][1]}]"
    if module.height < DIMENSION_LIMITS["height"][0] or module.height > DIMENSION_LIMITS["height"][1]:
        return False, f"height={module.height} вне [{DIMENSION_LIMITS['height'][0]}..{DIMENSION_LIMITS['height'][1]}]"
    if module.quantity < DIMENSION_LIMITS["quantity"][0] or module.quantity > DIMENSION_LIMITS["quantity"][1]:
        return False, f"quantity={module.quantity} вне [{DIMENSION_LIMITS['quantity'][0]}..{DIMENSION_LIMITS['quantity'][1]}]"
    # Угловой модуль должен быть квадратным
    if module.is_corner and module.width != module.depth:
        return False, f"угловой модуль не квадратный: {module.width}≠{module.depth}"
    # Высота должна соответствовать типу
    if module.type == "lower_base" and module.height > 1000:
        return False, f"нижняя база слишком высокая: {module.height}мм"
    if module.type == "upper_base" and module.height > 1200:
        return False, f"верхняя база слишком высокая: {module.height}мм"
    if module.type == "penal" and module.height < 1500:
        return False, f"пенал слишком низкий: {module.height}мм"
    return True, ""

# Применить в цикле:
for module_data in data.get("modules", []):
    ...
    is_valid, reason = _validate_module(module)
    if not is_valid:
        logger.warning(f"⚠️ Пропущен модуль: {reason}, data={module_data}")
        continue
    modules.append(module)
```

**Ожидаемый эффект:** −50% бредовых модулей, расчёт количеств становится стабильнее.

---

### 1.3 Умный выбор страниц для Vision-анализа

**Файл:** `app/services/full_pipeline.py`  
**Строки:** ~178–188 (метод `_pick_key_pages`)

**Проблема:** Сейчас берутся страницы 1..N-2 вслепую. Титульные листы, «Содержание», «Ведомость», штампы — всё уходит в Vision, тратятся токены и время.

**Решение:** Отдавать в Vision только страницы, где OCR реально обнаружил размеры или слова-индикаторы чертежа.

```python
def _pick_key_pages(
    self, ocr_result: PDFParseResult, total: int
) -> List[int]:
    """Выбрать страницы, которые с высокой вероятностью содержат чертежи."""
    # Соберём все размеры из результата OCR
    pages_with_dims: set[int] = set()
    
    # Если у OCR есть информация о страницах для dimensions
    for dim in ocr_result.dimensions_found:
        # Ищем паттерны «стр. X» или «page X» рядом с размером
        # (упрощённо — если dimensions_found не привязаны к страницам,
        #  используем эвристику ниже)
        pass
    
    # Эвристика: страницы где есть МАСШТАБ, М1:, чертёжные обозначения
    DRAWING_INDICATORS = [
        "М1:", "М 1:", "М1:50", "М1:25", "М1:10", "М1:20",
        "ВИД СВЕРХУ", "ВИД СПЕРЕДИ", "РАЗРЕЗ", "фасад",
        "×", "мм", "Габарит", "габарит",
    ]
    
    # Страницы, которые НЕ чертежи
    SKIP_INDICATORS = [
        "СОДЕРЖАНИЕ", "ВЕДОМОСТЬ", "СПЕЦИФИКАЦИЯ", "ТИТУЛ",
        "ПРИМЕЧАНИЕ", "ПРИЕМАНИЕ", "УСЛОВНЫЕ ОБОЗНАЧЕНИЯ",
        "штамп", "печать",
    ]
    
    # GLM-OCR возвращает текст постранично? Если нет — fallback на эвристику
    # Пока: если total ≤ 5 — все, иначе страницы 1..total-2
    if total <= 5:
        return list(range(total))
    
    key_pages = []
    for page_num in range(total):
        # Пропускаем титульную (стр. 0) и последнюю (штамп)
        if page_num == 0 or page_num >= total - 1:
            continue
        key_pages.append(page_num)
    
    return key_pages  # Пока упрощённо; полная версия — в Фазе 5
```

> **Примечание:** Полноценная версия с анализом контента каждой страницы — в Фазе 5 (кросс-валидация с OCR).

---

### 1.4 Отчёт о незаполненных строках шаблона

**Файл:** `app/services/template_filler.py`  
**Строки:** ~280–308 (метод `fill_template_from_pipeline`)

**Проблема:** Если для материала не нашлась строка в шаблоне — она молча пропускается. Оператор не знает, что какой-то материал «потерялся».

**Решение:** Собирать список ненайденных строк и добавлять лист «⚠ Проблемы» в выходной Excel.

```python
# В fill_template_from_pipeline, в цикле по rooms, после заполнения:

# Собираем незаполненные строки
unfilled_items = []
for keywords, field, unit, multiplier in ROW_MAPPING:
    value = qty_map.get(field)
    if value is not None and value > 0:
        row_num = _find_row_for_material(new_ws, keywords)
        if not row_num:
            unfilled_items.append({
                "room": sheet_name,
                "material": " + ".join(keywords[:2]),
                "expected_value": f"{value * multiplier} {unit}",
                "field": field,
            })

# Если есть незаполненные — пишем на лист «⚠ Проблемы»
if unfilled_items:
    _write_problems_sheet(wb, unfilled_items)


# Новая функция:
def _write_problems_sheet(wb: Workbook, items: List[dict]):
    """Создать/обновить лист с незаполненными позициями."""
    sheet_name = "⚠ Проблемы"
    if sheet_name in [ws.title for ws in wb.worksheets]:
        ws = wb[sheet_name]
    else:
        ws = wb.create_sheet(sheet_name)
    
    # Заголовки
    ws["A1"] = "Помещение"
    ws["B1"] = "Материал (не найдена строка в шаблоне)"
    ws["C1"] = "Ожидаемое значение"
    ws["D1"] = "Действие оператора"
    
    for i, item in enumerate(items, 2):
        ws.cell(row=i, column=1, value=item["room"])
        ws.cell(row=i, column=2, value=item["material"])
        ws.cell(row=i, column=3, value=item["expected_value"])
        ws.cell(row=i, column=4, value="Внести вручную или добавить строку в шаблон")
```

**Ожидаемый эффект:** оператор видит все пропуски, может внести вручную. Прозрачность 100%.

---

### 1.5 Русская буква «х» в размерах

**Файл:** `app/services/pdf_parser.py`  
**Строки:** ~350–353 (метод `_parse_specifications`, `size_patterns`)

**Проблема:** На русских чертежах часто пишут `600х560х840` (русская «х»). Текущая регулярка это не ловит.

```python
# БЫЛО:
size_patterns = [
    re.compile(r'(\d{2,4})\s*[×xX\*]\s*(\d{2,4})\s*[×xX\*]\s*(\d{2,4})'),
    re.compile(r'(\d{2,4})\s+[×xX\*]\s+(\d{2,4})'),
]

# СТАЛО:
size_patterns = [
    re.compile(r'(\d{2,4})\s*[×xX\*хХ]\s*(\d{2,4})\s*[×xX\*хХ]\s*(\d{2,4})'),
    re.compile(r'(\d{2,4})\s+[×xX\*хХ]\s+(\d{2,4})'),
]
```

---

## 🟡 ФАЗА 2 — Качество распознавания (2–3 дня)

> **Цель:** +20–30% точности распознавания модулей за счёт ансамбля моделей и улучшенных промптов.

### 2.1 Ансамбль из двух моделей с кросс-валидацией

**Файл:** `app/services/image_analyzer.py`  
**Новый метод:** `_ensemble_analyze`

**Идея:** Для страниц с `confidence: "low"` или `"medium"` — запросить вторую модель и сравнить результаты. Если совпадают — confidence повышается. Если расходятся — флаг оператору.

```python
async def _ensemble_analyze(
    self, image_path, primary_model, primary_provider
) -> RecognitionResult:
    """
    Ансамбль: основная модель + fallback, кросс-валидация.
    """
    # 1. Основная модель
    result1 = await self._try_analyze(image_path, primary_model, primary_provider)
    
    if result1.confidence == "high" and len(result1.modules) >= 2:
        return result1  # высокая уверенность → не переспрашиваем
    
    # 2. Вторая модель (fallback)
    fallback_model, fallback_provider = self.FALLBACK_CHAIN[0]
    if fallback_model == primary_model:
        return result1  # нечем переспрашивать
    
    result2 = await self._try_analyze(image_path, fallback_model, fallback_provider)
    
    # 3. Сравнение
    if not result2.modules:
        return result1  # вторая модель ничего не нашла
    
    # Сравниваем по типам и размерам
    match_count = 0
    for m1 in result1.modules:
        for m2 in result2.modules:
            if (m1.type == m2.type and 
                abs(m1.width - m2.width) <= 50 and
                abs(m1.depth - m2.depth) <= 20):
                match_count += 1
                break
    
    overlap = match_count / max(len(result1.modules), 1)
    
    if overlap >= 0.7:
        # Хорошее совпадение — повышаем confidence
        logger.info(f"🎯 Ансамбль: overlap={overlap:.0%}, confidence ↑")
        result1.confidence = "high"
        result1.notes = (result1.notes or "") + " [Ансамбль: высокая сходимость]"
    elif overlap >= 0.4:
        result1.confidence = "medium"
        result1.notes = (result1.notes or "") + " [Ансамбль: средняя сходимость]"
    else:
        result1.confidence = "low"
        result1.notes = (result1.notes or "") + " [Ансамбль: модели расходятся — нужна проверка]"
        # Добавляем модули из второго результата, которых нет в первом
        # ... (логика слияния)
    
    return result1
```

**Ожидаемый эффект:** +20–30% точности для сложных чертежей; цена — ×1.5 токенов для ~30% страниц.

---

### 2.2 Предобработка изображений

**Файл:** `app/services/image_analyzer.py`  
**Метод:** `_try_analyze`, строки 283–300

**Проблема:** Чертежи часто блёклые, неконтрастные, с мелкими цифрами. Простой ресайз до 2048px «съедает» детали.

**Решение:** Добавить повышение контраста и резкости ПЕРЕД ресайзом.

```python
# В _try_analyze, заменить блок загрузки изображения (строки 283-300):

# 1. Загружаем и оптимизируем изображение
image = Image.open(image_path)
logger.info(f"Изображение: {image.size}, mode={image.mode}")

# 1a. Повышаем контраст (цифры становятся читаемее)
from PIL import ImageEnhance, ImageFilter
enhancer = ImageEnhance.Contrast(image)
image = enhancer.enhance(1.3)  # +30% контраст

# 1b. Лёгкое повышение резкости (границы размерных линий)
image = image.filter(ImageFilter.SHARPEN)

# 1c. Если изображение слишком большое — ресайзим
max_size = 2048
if image.width > max_size or image.height > max_size:
    ratio = min(max_size / image.width, max_size / image.height)
    image = image.resize(
        (int(image.width * ratio), int(image.height * ratio)),
        Image.Resampling.LANCZOS
    )
    logger.info(f"Уменьшено до: {image.size}")
```

---

### 2.3 Унификация промптов с few-shot примерами

**Файл:** `app/services/image_analyzer.py`  
**Строки:** ~67–167 (константы промптов)

**Проблема:** Три разных промпта для трёх моделей — разный стиль, разные инструкции. Модели «путаются» в формате.

**Решение:** Единый промпт с 1–2 few-shot примерами (реальные успешные распознавания) и строгой JSON-схемой.

```python
UNIFIED_PROMPT = """Ты — парсер мебельных чертежей. Извлеки ВСЕ модули в JSON. Никакого текста вне JSON.

ТИПЫ МОДУЛЕЙ:
- lower_base: напольный (высота 700-900мм, глубина 500-600мм)
- upper_base: навесной (высота 600-1000мм, глубина 280-350мм)
- penal: высокий шкаф от пола (высота 1800-2500мм)
- corner: угловой, КВАДРАТНЫЙ (ширина=глубина, 600×600 или 900×900)

ПРИМЕР 1 — прямая кухня:
{"zone_type":"kitchen","materials":["EGGER H1379"],"modules":[{"type":"lower_base","width":600,"depth":560,"height":820,"quantity":3,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":1,"is_corner":false},{"type":"upper_base","width":600,"depth":320,"height":720,"quantity":2,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":1,"is_corner":false}],"confidence":"high","notes":""}

ПРИМЕР 2 — угловая кухня:
{"zone_type":"kitchen","materials":["EGGER H1379","МДФ"],"modules":[{"type":"corner","width":900,"depth":900,"height":820,"quantity":1,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":0,"is_corner":true},{"type":"lower_base","width":600,"depth":560,"height":820,"quantity":2,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":1},"shelves":1,"is_corner":false}],"confidence":"high","notes":""}

ПРАВИЛА:
- Размеры ТОЛЬКО в мм, целые числа
- УГЛОВОЙ модуль — ВСЕГДА width=depth, is_corner=true, ОДИН модуль
- Если на чертеже указано ×3 — quantity=3
- Если размеров нет — используй стандартные (низ: 560×820, верх: 320×720, пенал: 560×2100)
- json в двойных кавычках, без trailing commas
"""
```

---

### 2.4 Повторная попытка с уточняющим промптом при low confidence

**Файл:** `app/services/image_analyzer.py`  
**Метод:** `analyze_drawing` (строка 217)

**Идея:** Если результат `confidence="low"` — сделать второй запрос с более детальным промптом (например, «перечисли ВСЕ видимые размеры на чертеже числом»).

```python
# В analyze_drawing, после получения результата с low confidence:
if result.confidence == "low" and len(models_to_try) < max_retries:
    # Пробуем с уточняющим промптом
    logger.info("Low confidence — пробуем детальный промпт...")
    retry_result = await self._try_analyze_with_detailed_prompt(image_path, model, provider)
    if retry_result.modules:
        result = retry_result
```

---

## 🟡 ФАЗА 3 — Точность расчёта (2–3 дня)

> **Цель:** Устранить систематические ошибки в quantities, снизить ручную корректировку сметы.

### 3.1 Единая функция детекции материала

**Файлы:** `app/services/quantity_calc.py` + `app/services/template_filler.py`

**Проблема:** Логика «текстура или однотон?» размазана по трём местам:
- `calculate_quantities`:116-124
- `_build_quantity_map`:135-144
- `fill_template_for_room`:340-352

Малейшее изменение в правилах — нужно править три места.

**Решение:** Одна функция в `quantity_calc.py`, все остальные импортируют её.

```python
# quantity_calc.py — НОВАЯ ФУНКЦИЯ:

def detect_material_properties(materials: List[str]) -> dict:
    """
    Единая точка определения свойств материала.
    
    Returns:
        {"surface": "plain"|"texture", 
         "brand": "EGGER"|"EXTRAVERT"|"LAMARTY"|"ТОМЛЕСДРЕВ"|"unknown",
         "facade_type": "pvh"|"emdiway"|"paint_matte"|"paint_gloss"|"unknown",
         "has_glass": bool,
         "raw_materials": [...]}
    """
    result = {
        "surface": "plain",
        "brand": "unknown",
        "facade_type": "unknown",
        "has_glass": False,
        "raw_materials": materials,
    }
    
    materials_upper = " ".join(m.upper() for m in materials)
    
    # Бренд ЛДСП
    for brand in ["EGGER", "EXTRAVERT", "LAMARTY"]:
        if brand in materials_upper:
            result["brand"] = brand
            break
    if "ТОМЛЕСДРЕВ" in materials_upper:
        result["brand"] = "ТОМЛЕСДРЕВ"
    
    # Текстура vs однотон
    TEXTURE_KEYWORDS = ["ТЕКСТУР", "ДРЕВЕСН", "WOOD", "ДУБ", "ОРЕХ", "ЯСЕНЬ"]
    if any(kw in materials_upper for kw in TEXTURE_KEYWORDS):
        result["surface"] = "texture"
    elif any(m.upper().startswith(p) for m in materials for p in ["H1", "H3"]):
        result["surface"] = "texture"
    
    # Тип фасада
    if "EMDIWAY" in materials_upper:
        result["facade_type"] = "emdiway_titan" if "TITAN" in materials_upper else "emdiway"
    elif any(kw in materials_upper for kw in ["ЛАКОКРАСКА", "МАТОВЫЙ"]):
        result["facade_type"] = "paint_matte"
    elif "ГЛЯНЕЦ" in materials_upper:
        result["facade_type"] = "paint_gloss"
    elif "ПВХ" in materials_upper:
        result["facade_type"] = "pvh"
    
    # Стекло
    result["has_glass"] = any(kw in materials_upper for kw in ["СТЕКЛО", "ЗЕРКАЛО", "GLASS", "MIRROR"])
    
    return result
```

**Заменить** все три места на вызов `detect_material_properties(materials)`.

---

### 3.2 Учёт смежных стенок модулей

**Файл:** `app/services/quantity_calc.py`  
**Метод:** `calculate_quantities`

**Идея:** Два соседних модуля одинаковой глубины делят боковину — экономия ~0.011 м² ЛДСП на каждый стык.

```python
# В calculate_quantities, после цикла по модулям:

# Учёт смежных стенок
# Сортируем модули по типу и размерам, ищем соседей с одинаковой глубиной
SHARED_SIDE_SAVINGS_M2 = 0.0112  # 560мм × 20мм (толщина ЛДСП) в м²

modules_by_type: Dict[str, List[RecognizedModule]] = {}
for m in modules:
    modules_by_type.setdefault(m.type, []).append(m)

shared_pairs = 0
for mods in modules_by_type.values():
    # Модули одного типа с одинаковой глубиной = потенциально смежные
    for i in range(len(mods) - 1):
        if mods[i].depth == mods[i + 1].depth:
            shared_pairs += 1

if shared_pairs > 0:
    savings = shared_pairs * SHARED_SIDE_SAVINGS_M2
    q.ldsp_area_m2 -= savings
    logger.info(f"Учтено {shared_pairs} смежных стенок, экономия {savings:.2f} м² ЛДСП")
```

---

### 3.3 Настраиваемые эвристики (авто-аксессуары)

**Файл:** `app/services/quantity_calc.py`  
**Строки:** ~270–304 (блок эргономики/рекомендаций)

**Проблема:** Эвристики «если кухня → сушка + лоток + 2 ящика» не всегда верны. Для нестандартных проектов нужно отключать.

**Решение:** Вынести в параметры и добавить флаг `auto_accessories`.

```python
def calculate_quantities(
    modules: List[RecognizedModule],
    room_name: str = "",
    materials: List[str] = None,
    *,
    auto_accessories: bool = True,   # ← НОВЫЙ ПАРАМЕТР
    auto_drawers: bool = True,
    auto_led: bool = True,
) -> MaterialQuantities:
    ...

    if is_kitchen and auto_accessories:
        # ... текущая логика ...
    
    if is_kitchen and auto_drawers and q.drawers_count == 0:
        # ... текущая логика ...
```

---

## 🟢 ФАЗА 4 — Надёжность заполнения шаблона (1–2 дня)

> **Цель:** Исключить ситуацию «строка не найдена» и сделать маппинг гибким.

### 4.1 Нечёткий поиск строк (fuzzy matching)

**Файл:** `app/services/template_filler.py`  
**Новая функция рядом с `_find_row_for_material`**

```python
from difflib import SequenceMatcher

def _find_row_for_material_fuzzy(
    ws, keywords: List[str], threshold: float = 0.80
) -> Optional[int]:
    """
    Найти строку с нечётким совпадением (fuzzy).
    Используется как fallback, если точный поиск не дал результата.
    """
    query = " ".join(keywords).lower()
    best_row, best_score = None, 0
    
    for row in range(1, ws.max_row + 1):
        cell_a = str(ws.cell(row=row, column=1).value or "")
        cell_b = str(ws.cell(row=row, column=2).value or "")
        combined = f"{cell_a} | {cell_b}".lower()
        
        if not cell_a and not cell_b:
            continue
        
        score = SequenceMatcher(None, query, combined).ratio()
        if score > best_score:
            best_score, best_row = score, row
    
    if best_score >= threshold and best_row:
        logger.info(f"🎯 Fuzzy match: «{query[:50]}...» → R{best_row} (score={best_score:.2f})")
        return best_row
    
    return None


# В fill_template_from_pipeline, изменить логику поиска:
def _find_row_smart(ws, keywords):
    """Умный поиск: точный → нечёткий."""
    row = _find_row_for_material(ws, keywords)
    if row:
        return row
    # fallback — нечёткий
    return _find_row_for_material_fuzzy(ws, keywords, threshold=0.75)
```

---

### 4.2 Валидация шаблона (pre-flight check)

**Файл:** `app/services/template_filler.py`  
**Новая функция**

```python
def validate_template(template_path: str) -> dict:
    """
    Проверить шаблон ДО заполнения: все ли ожидаемые строки присутствуют.
    
    Returns:
        {"valid": bool, "missing": [...], "warnings": [...]}
    """
    from openpyxl import load_workbook
    
    wb = load_workbook(template_path)
    
    if "Рассчет" not in [ws.title for ws in wb.worksheets]:
        return {"valid": False, "missing": [], "warnings": ["Нет листа «Рассчет»"]}
    
    ws = wb["Рассчет"]
    missing = []
    found = []
    
    for keywords, field, unit, multiplier in ROW_MAPPING:
        row = _find_row_for_material(ws, keywords)
        if row:
            found.append((keywords[0], row))
        else:
            # Пробуем нечёткий поиск
            fuzzy_row = _find_row_for_material_fuzzy(ws, keywords, threshold=0.75)
            if fuzzy_row:
                found.append((f"{keywords[0]} (fuzzy)", fuzzy_row))
            else:
                missing.append(" + ".join(keywords[:2]))
    
    return {
        "valid": len(missing) == 0,
        "missing": missing,
        "found_count": len(found),
        "total_expected": len(ROW_MAPPING),
    }
```

---

### 4.3 Внешний конфиг ROW_MAPPING (JSON)

**Файлы:** новый `templates/row_mapping.json` + `app/services/template_filler.py`

**Проблема:** ROW_MAPPING захардкожен в коде. При смене шаблона (новый поставщик, новый формат) нужно лезть в Python.

**Решение:** Вынести маппинг в JSON, загружать при старте. Код остаётся как fallback.

```json
// templates/row_mapping.json
{
  "version": "1.0",
  "mappings": [
    {
      "keywords": ["EGGER ЛДСП", "однотон"],
      "field": "ldsp_sheets_plain",
      "unit": "листов",
      "multiplier": 1.0
    },
    {
      "keywords": ["EGGER ЛДСП", "текстура"],
      "field": "ldsp_sheets_texture",
      "unit": "листов",
      "multiplier": 1.0
    }
    // ... остальные строки
  ]
}
```

```python
# template_filler.py — загрузка:
import json

def _load_row_mapping(config_path: Optional[str] = None) -> List[Tuple]:
    """Загрузить маппинг из JSON или использовать хардкод."""
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [
            (item["keywords"], item["field"], item["unit"], item["multiplier"])
            for item in data["mappings"]
        ]
    return ROW_MAPPING  # fallback на хардкод
```

---

## 🔵 ФАЗА 5 — Кросс-валидация и Quality Gates (2–3 дня)

> **Цель:** Автоматически выявлять расхождения между OCR и Vision, давать оператору инструмент для быстрой проверки.

### 5.1 Кросс-валидация OCR ↔ Vision

**Файл:** `app/services/full_pipeline.py`  
**Метод:** `process` (после строки 148, перед сборкой результата)

```python
# В process(), после обработки всех страниц:

# Кросс-валидация: сравниваем OCR-спецификации и Vision-модули
for page_num, spec in room_specs.items():
    ocr_room = self._find_room_for_page(ocr_result, page_num)
    if ocr_room:
        # Сколько модулей ожидалось по OCR?
        ocr_module_count = len(ocr_room.dimensions)
        vision_module_count = len(spec.modules)
        
        if ocr_module_count > 0 and abs(ocr_module_count - vision_module_count) >= 2:
            spec.notes.append(
                f"⚠ Расхождение OCR/Vision: OCR={ocr_module_count}, "
                f"Vision={vision_module_count}. Проверьте!"
            )
            logger.warning(
                f"{spec.room_name}: OCR={ocr_module_count} размеров, "
                f"Vision={vision_module_count} модулей"
            )
```

---

### 5.2 Quality Score для каждого помещения

**Файл:** `app/services/full_pipeline.py`  
**В `RoomSpec`** и **`PipelineResult`**

```python
@dataclass
class RoomSpec:
    # ... существующие поля ...
    quality_score: float = 0.0  # 0..1
    quality_flags: List[str] = field(default_factory=list)


def _calculate_quality(room: RoomSpec) -> float:
    """
    Рассчитать оценку качества распознавания для помещения.
    1.0 = идеально, 0.0 = полностью ошибочно.
    """
    score = 1.0
    flags = []
    
    # Confidence влияет
    if room.confidence == "low":
        score -= 0.4
        flags.append("Низкая уверенность AI")
    elif room.confidence == "medium":
        score -= 0.2
    
    # Нет модулей
    if not room.modules:
        score -= 0.5
        flags.append("Модули не найдены")
    
    # Подозрительно мало модулей для кухни
    if "кухн" in room.room_name.lower():
        if len(room.modules) < 2:
            score -= 0.2
            flags.append("Слишком мало модулей для кухни")
    
    # Все модули одинакового размера — подозрительно
    if len(room.modules) >= 3:
        unique_sizes = set((m.width, m.depth, m.height) for m in room.modules)
        if len(unique_sizes) == 1:
            score -= 0.15
            flags.append("Все модули одного размера — возможно дубликат")
    
    room.quality_score = max(0, score)
    room.quality_flags = flags
    return room.quality_score
```

---

### 5.3 Сводный лист «Контроль качества» в выходном Excel

**Файл:** `app/services/template_filler.py`  
**После** `fill_template_from_pipeline`, перед сохранением:

```python
def _add_quality_sheet(wb: Workbook, pipeline_result: PipelineResult):
    """Добавить лист контроля качества."""
    ws = wb.create_sheet("✅ Контроль качества")
    
    # Заголовки
    headers = ["Помещение", "Страница", "Модулей", "Уверенность", 
               "Quality Score", "Флаги", "Рекомендация"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(name="Arial", size=10, bold=True)
    
    for i, room in enumerate(pipeline_result.rooms, 2):
        ws.cell(row=i, column=1, value=room.room_name)
        ws.cell(row=i, column=2, value=room.page)
        ws.cell(row=i, column=3, value=len(room.modules))
        ws.cell(row=i, column=4, value=room.confidence)
        ws.cell(row=i, column=5, value=f"{room.quality_score:.0%}")
        ws.cell(row=i, column=6, value="; ".join(room.quality_flags))
        
        # Рекомендация оператору
        if room.quality_score < 0.5:
            ws.cell(row=i, column=7, value="🔴 ПЕРЕПРОВЕРИТЬ ВРУЧНУЮ")
        elif room.quality_score < 0.8:
            ws.cell(row=i, column=7, value="🟡 Желательно проверить")
        else:
            ws.cell(row=i, column=7, value="🟢 ОК")
```

---

### 5.4 Умный выбор страниц (полная версия)

**Файл:** `app/services/full_pipeline.py`  
**Метод:** `_pick_key_pages` (замена упрощённой версии из Фазы 1.3)

**Идея:** GLM-OCR может вернуть Markdown с разбивкой по страницам. Если формат ответа это позволяет — анализируем контент каждой страницы на наличие признаков чертежа.

```python
def _pick_key_pages(self, ocr_result: PDFParseResult, total: int) -> List[int]:
    """Полная версия: анализируем контент каждой страницы."""
    if total <= 5:
        return list(range(total))
    
    # Собираем текст по страницам (если GLM-OCR разбивает по страницам)
    # Формат может быть: "## Страница 1\n...\n## Страница 2\n..."
    page_texts = self._split_by_pages(ocr_result)
    
    DRAWING_SIGNALS = re.compile(
        r'М\d*:?\d+|масштаб|×|х|мм|габарит|фасад|разрез|вид сверху|план',
        re.IGNORECASE
    )
    SKIP_SIGNALS = re.compile(
        r'содержание|ведомость|спецификация|титул|примечание|приемание|условные обозначения',
        re.IGNORECASE
    )
    
    key_pages = []
    for page_num in range(total):
        text = page_texts.get(page_num, "")
        
        # Пропускаем титульную и последнюю
        if page_num == 0 or page_num >= total - 1:
            continue
        
        # Пропускаем явно не-чертежи
        if SKIP_SIGNALS.search(text) and not DRAWING_SIGNALS.search(text):
            logger.info(f"  Стр. {page_num + 1}: пропущена (не чертёж)")
            continue
        
        # Берём если есть признаки чертежа
        if DRAWING_SIGNALS.search(text) or not text:
            key_pages.append(page_num)
    
    return key_pages if key_pages else list(range(1, total - 1))
```

---

## 📊 График внедрения

```
Неделя 1:
  День 1-2:  Фаза 1 (1.1–1.5) — быстрые победы
  День 3-4:  Фаза 2 (2.1, 2.2) — ансамбль + предобработка
  День 5:    Фаза 2 (2.3, 2.4) — промпты + повторные попытки

Неделя 2:
  День 6-7:  Фаза 3 (3.1, 3.2) — рефакторинг + смежные стенки
  День 8:    Фаза 3 (3.3) + Фаза 4 (4.1) — эвристики + fuzzy matching

Неделя 3:
  День 9-10: Фаза 4 (4.2, 4.3) — валидация шаблона + JSON-конфиг
  День 11-12: Фаза 5 (5.1–5.4) — кросс-валидация + quality gates
  День 13:   Тестирование на 3–5 реальных проектах, замер точности
```

---

## 🎯 Метрики успеха

| Метрика | Сейчас | Цель |
|---------|--------|------|
| Доля ошибок парсинга JSON | ~15% | <2% |
| Доля модулей с нереалистичными размерами | ~10% | <1% |
| Доля страниц, требующих ручной проверки | ~30% | <10% |
| Точность заполнения колонки E (количество) | ~80% | >95% |
| Доля «потерянных» строк (не найдена в шаблоне) | ~8% | <2% |
| Время оператора на 1 проект (проверка + правки) | ~20 мин | <5 мин |

---

## ⚠️ Риски

| Риск | Вероятность | Митигация |
|------|------------|-----------|
| `response_format: json_object` не поддерживается GLM-4.6V | Низкая | Проверить в документации Z.ai; fallback — `response_format` только для OpenRouter |
| Ансамбль удваивает время обработки | Средняя | Запускать вторую модель только для low-confidence страниц (~30%) |
| Нечёткий поиск даёт ложные срабатывания | Средняя | Использовать ТОЛЬКО как fallback после точного; порог 0.80 |
| Изменение формата ответа GLM-OCR ломает парсинг страниц | Средняя | Сохранять примеры ответов для тестов; мониторить в логах |

---

## 📝 Чек-лист для приёмки каждой фазы

- [ ] Код проходит `python -m app.main` без ошибок
- [ ] Все существующие тесты (если есть) проходят
- [ ] Прогнать на 2–3 реальных PDF-проектах из папки `ПРОЕКТЫ/`
- [ ] Сравнить Excel «до» и «после» — заполнение строк
- [ ] Проверить логи на наличие новых warning
- [ ] Закоммитить с осмысленным сообщением
