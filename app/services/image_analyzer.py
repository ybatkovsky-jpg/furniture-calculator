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
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from dataclasses import dataclass, field

import httpx
from PIL import Image

from app.services.scale_calc import calculate_scaled_facades, ScaledFacade
from app.services.furniture_defaults import get_rules
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


@dataclass
class FacadeData:
    """Данные одного фасада (дверцы)."""
    width_mm: int
    height_mm: int
    zone: str          # "lower" / "upper" / "penal"
    is_corner: bool = False


@dataclass
class FacadeResult:
    """Результат распознавания фасадов."""
    facades: List[FacadeData] = field(default_factory=list)
    confidence: str = "low"
    zone_type: Optional[str] = None
    materials_mentioned: List[str] = field(default_factory=list)
    model_used: Optional[str] = None
    notes: Optional[str] = None


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
★ Горизонтальная линия внутри нижней базы = деление на ящики. ВСЯ база — ОДИН модуль, укажи drawers.count = количеству ящиков. НЕ создавай два модуля!
★ Вертикальная линия только в зоне фасада (не доходит до столешницы) = стык двух дверей. Это ОДИН модуль с facades.count=2.
★ Вертикальная линия через ВЕСЬ корпус (от столешницы до пола) = граница между РАЗНЫМИ модулями.
★ Пеналы: на всю высоту кухни, всегда с краю. Левый — часто под холодильник, правый — для коммуникаций.

ОБЩИЕ ПРАВИЛА (нарушение = брак):
1. Один физический корпус = ОДИН модуль. Две дверцы на одном корпусе = 1 модуль с facades.count=2.
2. Шкаф с несколькими фасадами НЕ дробить на несколько модулей.
3. Размерные линии и выноски — НЕ модули, игнорируй их.
4. Планки-заполнители (40-80мм) — НЕ модули, игнорируй.
5. Модуль уже 150мм — скорее всего ошибка: проверь, не дверца ли это.
6. Если точный размер не читается — стандартный (низ: 560×820, верх: 320×720, пенал: 560×2500).
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


# ФАСАДНЫЙ ПРОМПТ — для точного подсчёта фасадов и петель
# Используется Qwen3-VL-235B-Thinking (основная модель с 2026-07)
FACADE_PROMPT = """Ты — конструктор-технолог мебельной фабрики. Проанализируй чертёж и перечисли ВСЕ фасады (дверцы) в JSON.

Фасад = видимая дверца шкафа с ручкой или треугольником открывания.

Для КАЖДОГО фасада укажи:
- width_mm: ширина. Бери с размерной линии. Если размерной линии нет — стандарт: низ/верх 600мм, пенал 600мм
- height_mm: высота. Бери с размерной линии. Если размерной линии нет — стандарт: низ 716мм, верх 596мм, пенал 2500мм
- zone: "lower", "upper", "penal"
- is_corner: true только для углового

ПРОВЕРКА НА ГИГАНТОВ/ЛИЛИПУТОВ:
- Ширина фасада должна быть 250-1200мм. Если получилось меньше 250 или больше 1200 — перепроверь
- Высота нижних 700-900мм, верхних 500-800мм, пеналов 1800-2800мм
- Если размеры выходят за эти пределы — используй стандартные

ПРАВИЛА:
- Каждая видимая дверца = ОДИН фасад
- Пенал: сколько фасадов видишь на чертеже — столько и укажи
- Планки-заполнители — НЕ фасады, игнорируй
- Размеры в мм, целыми числами

ОПРЕДЕЛИ: zone_type, materials, confidence

Верни ТОЛЬКО JSON: {"zone_type":"...","materials":[],"facades":[{"width_mm":600,"height_mm":716,"zone":"lower"},...],"confidence":"...","notes":""}"""


# ПРОМПТ С МАСШТАБОМ — модель даёт bbox, Python считает размеры
SCALE_PROMPT = """Ты — конструктор-технолог мебельной фабрики. Проанализируй чертёж и перечисли ВСЕ фасады (дверцы) с их положением на изображении.

Для КАЖДОГО фасада укажи:
- zone: "lower", "upper", "penal"
- bbox_x_pct: положение левого края фасада в процентах от ширины ИЗОБРАЖЕНИЯ (0-100)
- bbox_w_pct: ширина фасада в процентах от ширины ИЗОБРАЖЕНИЯ (0-100)
- Пеналы указывай отдельно (они не входят в общую ширину нижних/верхних)

Также найди на чертеже ОБЩИЙ ГАБАРИТ нижних баз:
- total_width_mm: общая ширина ВСЕХ нижних баз в мм (цифра с размерной линии)
- Если габарит не указан одной цифрой — сложи все размеры нижних фасадов с чертежа

ЯЩИКИ: горизонтальная линия внутри нижнего фасада = ящики. Если видишь такие линии — укажи номера этих фасадов (начиная с 1) в drawer_indices. Например, если фасады 2 и 3 имеют горизонтальные линии внутри: "drawer_indices": [2, 3]. Если ящиков нет — "drawer_indices": [].

ПРАВИЛА:
- Каждая видимая дверца = ОДИН фасад
- Планки-заполнители — НЕ фасады, игнорируй
- Проценты оценивай визуально, не нужно считать пиксели

ОПРЕДЕЛИ: zone_type, materials, confidence

Верни ТОЛЬКО JSON:
{"zone_type":"...","materials":[],"total_width_mm":3000,
 "facades":[{"zone":"lower","bbox_x_pct":5,"bbox_w_pct":20},...],
 "drawer_indices":[],
 "confidence":"...","notes":""}"""

# ЕДИНЫЙ ПРОМПТ v2 (2026-07-12)
# Объединяет bbox-проценты + типы модулей + материалы в одном запросе.
# Модель читает ОДНО число (total_width_mm) с размерной линии,
# Python вычисляет точные мм через scale_calc.
# ================================================================

_UNIFIED_PROMPT_V2_EMBEDDED = """Ты — конструктор-технолог мебельной фабрики. Проанализируй чертёж мебели (кухня/шкаф ИЛИ отдельное изделие без дверей: кровать, стол, диван, тумба, стеллаж) и верни структурированный JSON по одному изделию на листе.

═══════════════════════════════════════
ТИПЫ МЕБЕЛИ (строго)
═══════════════════════════════════════
- lower_base — напольный модуль (на полу, H=700-900, D=500-600)
- upper_base — навесной модуль (на стене, H=600-1000, D=280-350)
- penal — высокий шкаф от пола до потолка (H=1800-2800, D=500-600). ВСЕГДА С КРАЮ!
- corner — УГЛОВОЙ модуль. ВСЕГДА квадратный: W=D (600×600, 900×900, 1000×1000). ЭТО ОДИН МОДУЛЬ!

═══════════════════════════════════════
УСЛОВНЫЕ ОБОЗНАЧЕНИЯ НА ЧЕРТЕЖЕ
═══════════════════════════════════════
★ Пунктирные треугольники на дверце = направление открывания. Это НЕ отдельный модуль, это одна дверца!
★ Горизонтальная линия внутри нижней базы = деление на ящики. ВСЯ база — ОДИН модуль.
★ Вертикальная линия только в зоне фасада (не доходит до столешницы) = стык двух дверей. ОДИН модуль, facades.count=2.
★ Вертикальная линия через ВЕСЬ корпус (от столешницы до пола) = граница между РАЗНЫМИ модулями.
★ Штриховка/сетка на фасаде = стекло → has_glass=true.
★ Пеналы: на всю высоту кухни, всегда с краю.
★ Шкаф-купе: створки идут внахлёст — КАЖДАЯ видимая створка = ОДИН элемент facades (считай по вертикальным стыкам рам створок); в notes ОБЯЗАТЕЛЬНО пиши «раздвижные двери».

═══════════════════════════════════════
СТАНДАРТНЫЕ РАЗМЕРЫ (если не читается)
═══════════════════════════════════════
| Тип         | Ширина  | Глубина | Высота |
|-------------|---------|---------|--------|
| lower_base  | 600     | 560     | 820    |
| upper_base  | 600     | 320     | 720    |
| penal       | 600     | 560     | 2500   |
| corner      | 900     | 900     | 820    |

ОБЩИЕ ПРАВИЛА (нарушение = брак):
1. Один физический корпус = ОДИН модуль. Две дверцы на одном корпусе = 1 модуль с facades.count=2.
2. Шкаф с несколькими фасадами НЕ дробить на несколько модулей.
3. Размерные линии и выноски — НЕ модули, игнорируй их.
4. Планки-заполнители (40-80мм) — НЕ модули и НЕ дверцы, игнорируй.
5. Модуль уже 150мм — скорее всего ошибка: проверь, не дверца ли это.
6. ВСЕ размеры в мм, целыми числами. Никаких "см" или "м".
7. Угловой модуль — всегда is_corner=true, W=D. Глубина угла НЕ бывает 320мм.
8. Пеналы считай отдельно — они не входят в группу нижних/верхних баз.
9. САНТЕХНИКА (раковина, стиральная/сушильная машина, ванна, полотенцесушитель, унитаз) на чертеже → помещение «Ванная» (санузел), даже если по компоновке похоже на кухню. Кухонная мойка — не санузел.
10. Широкие двери (700–1100мм) на прямой тумбе/ТВ-тумбе/комоде — обычные дверцы: is_corner=false. Угловая дверца ТОЛЬКО если видно, что модуль стоит в углу и W=D.

═══════════════════════════════════════
ЗАДАЧА 1: РАЗМЕРНАЯ ЛИНИЯ (total_width_mm)
═══════════════════════════════════════
Найди ОСНОВНУЮ размерную линию с одним числом — это габарит изделия по ШИРИНЕ.

Для разных изделий линия выглядит по-разному:
- Кухня/гарнитур: горизонтальная линия со СТРЕЛКАМИ (← →) над рядом нижних шкафов, охватывает ВСЕ нижние модули от левого до правого края.
- Шкаф-купе/гардероб/пенал: горизонтальная линия под или над корпусом — полная ширина одного шкафа.
- Прихожая/стеллаж: суммарная ширина всех модулей в ряду.
- Кровать/стол/диван/тумба без дверей: линия ширины (часто со стрелками ← →) под или над изделием — габарит по ширине. Читай её как основную.

ПРОВЕРЬ СЕБЯ:
- Число должно быть >400мм и <8000мм
- Если число <400 — это размер отдельной полки/дверцы, ищи линию над ВСЕМ изделием
- Если видишь несколько размерных линий — бери ту, что ДЛИННЕЕ всех
- Типичные значения: 600, 800, 1000, 1200, 1500, 1800, 2400, 3000, 3600, 4200, 4800
- Если размерная линия не читается — укажи total_width_mm=0

═══════════════════════════════════════
ЗАДАЧА 1Б: ВЫСОТЫ РЯДОВ (heights_mm)
═══════════════════════════════════════
Высоты читай с ВЕРТИКАЛЬНЫХ размерных линий ТАК ЖЕ, как ширину — с горизонтальных.
Для КАЖДОГО ряда дверей, который есть на чертеже, найди его вертикальную размерную линию с числом:
- lower (нижние базы): вертикальная линия у нижнего ряда (типично 700–900 мм)
- upper (верхние базы): вертикальная линия у навесного ряда (типично 550–750 мм)
- penal (пенал/высокий шкаф): вертикальная линия у пенала (типично 1800–2800 мм)

Верни объект "heights_mm" ТОЛЬКО с зонами, высоты которых прочитал с чертежа:
  "heights_mm": {"lower": 820, "upper": 720, "penal": 2500}

Правила:
- Нужна ВЫСОТА РЯДА = высота фасада/дверцы этого ряда (размер вертикальной размерной линии, приложенной К САМОМУ ряду, со стрелками сверху/снизу ряда). НЕ бери расстояние от пола до нижнего края верхних шкафов, от пола до потолка и т.п.
- Типичные высоты, чтобы проверить себя: кухонные нижние базы 600–900; навесные (upper) 450–1000; пенал/высокий шкаф 1800–2800; шкаф-купе створки 2000–2800.
- Если вертикальной размерной линии у ряда НЕТ или число вне типичного диапазона зоны — НЕ выдумывай: не включай ключ, Python возьмёт стандарт.
- Кровать/стол/диван/тумба без дверей: heights_mm НЕ возвращай (facades пуст).
- Числа целые, в мм. Без "см"/"м".

═══════════════════════════════════════
ЗАДАЧА 2: ПЕРЕЧИСЛИ ВСЕ ДВЕРЦЫ (facades)
═══════════════════════════════════════
Перечисли ВООБЩЕ ВСЕ видимые дверцы на чертеже. Каждая дверца = ОДИН элемент.
НЕ группируй! НЕ пропускай! Считай по одной, слева направо.

Для КАЖДОЙ дверцы:
- zone: "lower" | "upper" | "penal"
- bbox_x_pct: позиция ЛЕВОГО края в % от ширины ИЗОБРАЖЕНИЯ (0-100)
- bbox_w_pct: ШИРИНА в % от ширины ИЗОБРАЖЕНИЯ (0-100)
- is_corner: true ТОЛЬКО для угловой дверцы

ВАЖНО:
- Оценивай проценты с точностью до 1% (не округляй до 5% или 10%)
- Сумма bbox_w_pct всех НИЖНИХ дверей ≈ их доле на изображении
- Пеналы НЕ включай в сумму нижних (они с краю)
- Планки-заполнители (40-80мм) игнорируй
- Дверца с треугольником открывания = одна дверца (не модуль!)
- Дверца со штриховкой/сеткой = стекло → has_glass_facades
- Дверей на чертеже НЕТ (кровать, стол, диван, тумба/стеллаж без дверей, открытые полки)? Тогда facades: [] — пустой массив. НЕ выдумывай дверцы!
- Шкаф-купе: каждая раздвижная створка = ОДИН элемент facades — створки перекрываются внахлёст, считай по вертикальным стыкам рам.

═══════════════════════════════════════
ЗАДАЧА 3: ЯЩИКИ (drawer_indices)
═══════════════════════════════════════
Горизонтальная линия внутри фасада = деление на ящики (выдвижные ящики).
Ищи такие линии в ЛЮБОМ фасаде: нижние базы, пеналы, высокие шкафы-купе
(в шкафах-купе ящики часто в нижней зоне).

Если видишь горизонтальные линии внутри фасада — укажи НОМЕР этого фасада
(начиная с 1, в порядке перечисления в массиве facades) в drawer_indices.

Пример: если у фасадов №2 и №3 есть горизонтальные линии внутри:
"drawer_indices": [2, 3]
Если ящиков нет — "drawer_indices": []

═══════════════════════════════════════
МЕБЕЛЬ БЕЗ ДВЕРЦ (кровать, стол, диван, тумба без дверей)
═══════════════════════════════════════
На листе может быть изделие БЕЗ распашных дверей. Если фасады на чертеже НЕ нарисованы — их НЕТ, дверцы НЕ выдумывать!
Вертикальные линии внутри такого изделия — рёбра каркаса/царги, НЕ стыки дверей.

К изделиям без дверей относятся:
- Кровать (каркас, ламели, царги, изголовье, матрас) — дверец нет
- Стол / письменный стол (столешница на опорах) — дверец нет
- Диван / кресло (мягкая мебель) — дверец нет
- Тумба / комод / стеллаж БЕЗ дверей (открытые ячейки и полки) — дверец нет

Правила для мебели без дверей:
1. total_width_mm — прочитай ОСНОВНУЮ размерную линию (габарит по ширине, ЗАДАЧА 1). Линия видна → число НЕ 0.
2. facades: [] — ВСЕГДА пустой массив. НЕ превращай ячейки, полки, спинки, матрас и рёбра каркаса в дверцы!
3. has_glass_facades: [] и drawer_indices: [] — всегда пустые (ящики нумеруются по фасадам, а фасадов нет).
4. notes — обязательно опиши: тип + габарит Ш×Г, например: "кровать 2200×1400, каркас + ламели, дверец нет".
5. Тумба/шкаф С дверцами — это НЕ мебель без дверей: перечисляй дверцы как обычно (ЗАДАЧА 2).

═══════════════════════════════════════
ПРИМЕР 1: прямая кухня (5 дверей)
═══════════════════════════════════════
{"zone_type":"Кухня","materials":["EGGER H1379"],"heights_mm":{"lower":820,"upper":720},"total_width_mm":3000,
 "facades":[
   {"zone":"lower","bbox_x_pct":3,"bbox_w_pct":19,"is_corner":false},
   {"zone":"lower","bbox_x_pct":23,"bbox_w_pct":19,"is_corner":false},
   {"zone":"lower","bbox_x_pct":43,"bbox_w_pct":19,"is_corner":false},
   {"zone":"upper","bbox_x_pct":3,"bbox_w_pct":19,"is_corner":false},
   {"zone":"upper","bbox_x_pct":23,"bbox_w_pct":19,"is_corner":false}
 ],"has_glass_facades":[],"drawer_indices":[],"confidence":"high","notes":""}

═══════════════════════════════════════
ПРИМЕР 2: угловая кухня с пеналом (7 дверей)
═══════════════════════════════════════
{"zone_type":"Кухня","materials":["EGGER H3158","МДФ матовый"],"heights_mm":{"lower":820,"upper":720,"penal":2500},"total_width_mm":3300,
 "facades":[
   {"zone":"lower","bbox_x_pct":2,"bbox_w_pct":26,"is_corner":true},
   {"zone":"lower","bbox_x_pct":28,"bbox_w_pct":13,"is_corner":false},
   {"zone":"lower","bbox_x_pct":41,"bbox_w_pct":13,"is_corner":false},
   {"zone":"lower","bbox_x_pct":54,"bbox_w_pct":14,"is_corner":false},
   {"zone":"upper","bbox_x_pct":28,"bbox_w_pct":13,"is_corner":false},
   {"zone":"upper","bbox_x_pct":41,"bbox_w_pct":13,"is_corner":false},
   {"zone":"penal","bbox_x_pct":72,"bbox_w_pct":13,"is_corner":false}
 ],"has_glass_facades":[3,7],"drawer_indices":[2],"confidence":"high","notes":""}

═══════════════════════════════════════
ПРИМЕР 3: большая кухня (16 дверей)
═══════════════════════════════════════
{"zone_type":"Кухня","materials":["EGGER H1379","EGGER H3158"],"total_width_mm":3600,
 "facades":[
   {"zone":"lower","bbox_x_pct":2,"bbox_w_pct":10,"is_corner":false},
   {"zone":"lower","bbox_x_pct":12,"bbox_w_pct":10,"is_corner":false},
   {"zone":"lower","bbox_x_pct":22,"bbox_w_pct":10,"is_corner":false},
   {"zone":"lower","bbox_x_pct":32,"bbox_w_pct":10,"is_corner":false},
   {"zone":"lower","bbox_x_pct":42,"bbox_w_pct":10,"is_corner":false},
   {"zone":"lower","bbox_x_pct":52,"bbox_w_pct":10,"is_corner":false},
   {"zone":"upper","bbox_x_pct":2,"bbox_w_pct":9,"is_corner":false},
   {"zone":"upper","bbox_x_pct":11,"bbox_w_pct":9,"is_corner":false},
   {"zone":"upper","bbox_x_pct":20,"bbox_w_pct":9,"is_corner":false},
   {"zone":"upper","bbox_x_pct":29,"bbox_w_pct":9,"is_corner":false},
   {"zone":"upper","bbox_x_pct":38,"bbox_w_pct":9,"is_corner":false},
   {"zone":"upper","bbox_x_pct":47,"bbox_w_pct":9,"is_corner":false},
   {"zone":"upper","bbox_x_pct":56,"bbox_w_pct":9,"is_corner":false},
   {"zone":"penal","bbox_x_pct":70,"bbox_w_pct":10,"is_corner":false},
   {"zone":"penal","bbox_x_pct":80,"bbox_w_pct":10,"is_corner":false},
   {"zone":"penal","bbox_x_pct":91,"bbox_w_pct":10,"is_corner":false}
 ],"has_glass_facades":[1,9],"drawer_indices":[],"confidence":"high","notes":""}

═══════════════════════════════════════
ПРИМЕР 4: шкаф-купе в спальню (3 двери)
═══════════════════════════════════════
{"zone_type":"Спальня","materials":["EGGER H3158"],"total_width_mm":1800,
 "facades":[
   {"zone":"upper","bbox_x_pct":3,"bbox_w_pct":31,"is_corner":false},
   {"zone":"upper","bbox_x_pct":35,"bbox_w_pct":31,"is_corner":false},
   {"zone":"upper","bbox_x_pct":67,"bbox_w_pct":31,"is_corner":false}
 ],"has_glass_facades":[],"drawer_indices":[],"confidence":"high","notes":"Шкаф-купе 1800×600×2500, раздвижные двери"}

═══════════════════════════════════════
ПРИМЕР 5: гардеробная (4 модуля)
═══════════════════════════════════════
{"zone_type":"Гардеробная","materials":["LAMARTY H1185"],"total_width_mm":2400,
 "facades":[
   {"zone":"penal","bbox_x_pct":3,"bbox_w_pct":23,"is_corner":false},
   {"zone":"upper","bbox_x_pct":27,"bbox_w_pct":23,"is_corner":false},
   {"zone":"upper","bbox_x_pct":51,"bbox_w_pct":23,"is_corner":false},
   {"zone":"lower","bbox_x_pct":75,"bbox_w_pct":23,"is_corner":false}
 ],"has_glass_facades":[],"drawer_indices":[1,4],"confidence":"high","notes":"Гардеробная 2400×600, ящики в пенале №1 и нижнем модуле №4"}

═══════════════════════════════════════
ПРИМЕР 6: кровать в детской (без дверей)
═══════════════════════════════════════
{"zone_type":"Детская","materials":[],"total_width_mm":2200,"facades":[],"has_glass_facades":[],"drawer_indices":[],"confidence":"high","notes":"Кровать 2200×1400, каркас + ламели, дверец нет"}

═══════════════════════════════════════
ПРИМЕР 7: санузел с колонной под технику (2 двери)
═══════════════════════════════════════
{"zone_type":"Ванная","materials":["ЛДСП влагостойкий"],"heights_mm":{"penal":2480},"total_width_mm":1581,
 "facades":[
   {"zone":"penal","bbox_x_pct":6,"bbox_w_pct":20,"is_corner":false},
   {"zone":"upper","bbox_x_pct":28,"bbox_w_pct":9,"is_corner":false}
 ],"has_glass_facades":[],"drawer_indices":[],"confidence":"high","notes":"Санузел: колонна под стиральную/сушильную машину (ниша под технику без дверей) + верхний шкаф"}

═══════════════════════════════════════
ПРИМЕР 8: TV-тумба с двумя широкими дверцами
═══════════════════════════════════════
{"zone_type":"Гостиная","materials":["EGGER H3158"],"heights_mm":{"lower":300},"total_width_mm":1830,
 "facades":[
   {"zone":"lower","bbox_x_pct":3,"bbox_w_pct":42,"is_corner":false},
   {"zone":"lower","bbox_x_pct":46,"bbox_w_pct":42,"is_corner":false}
 ],"has_glass_facades":[],"drawer_indices":[2],"confidence":"high","notes":"TV-тумба 1830×300, две широкие дверцы — НЕ угловые модули"}

ОПРЕДЕЛИ ЗОНУ (строго одно из): Кухня, Гостиная, Спальня, Детская, Прихожая, Ванная, Гардеробная, Кабинет, Балкон, Столовая, Постирочная.

МАТЕРИАЛЫ: если указаны декоры (EGGER H1379, H3158 и т.п.) — перечисли в materials.

Верни ТОЛЬКО валидный JSON с полями: zone_type, materials, total_width_mm, facades, has_glass_facades, drawer_indices, confidence, notes.

heights_mm — ОПЦИОНАЛЬНОЕ поле (ЗАДАЧА 1Б): {"lower":820,"upper":720,"penal":2500}. Включай ТОЛЬКО зоны, высоту которых прочитал с ВЕРТИКАЛЬНОЙ размерной линии. Не читается → не включай ключ (Python возьмёт стандарт).

РУБРИКА CONFIDENCE (выбирай строго):
- "high": ОСНОВНАЯ размерная линия прочитана с чертежа И все дверцы пересчитаны по чертежу, одна за другой.
  Для мебели без дверей: "high", если total_width_mm прочитан с размерной линии (пустой facades:[] уверенность НЕ понижает).
- "medium": размеры частично оценены (по типовым значениям) ИЛИ часть дверей определена по косвенным признакам (не по чертежу).
- "low": в основном эвристики и предположения (ширина угадана, дверцы/размерные линии не читаются)."""


def _load_unified_prompt() -> str:
    """Читает промпт из prompts/unified.txt; при отсутствии/ошибке — встроенный.

    Файл позволяет править промпт без изменения кода (roadmap «Конфиги»).
    Пустой файл тоже откатывается на встроенный вариант.
    """
    path = Path(__file__).resolve().parents[2] / "prompts" / "unified.txt"
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError as exc:
        logger.warning("Не удалось прочитать %s (%s) — использую встроенный промпт", path, exc)
    return _UNIFIED_PROMPT_V2_EMBEDDED


UNIFIED_PROMPT_V2 = _load_unified_prompt()


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
    Постобработка списка модулей: убрать мусор и сгруппировать дубликаты.

    Стратегия (консервативная — precision > recall):
    1. Группировка ТОЛЬКО точных дублей (одинаковые W×D×H×type) → quantity
    2. НЕ сливаем модули разной ширины (это разные шкафы!)
    3. Модули <250мм уже отфильтрованы _validate_module
    """
    if len(modules) <= 1:
        return list(modules)

    # Группируем точные дубликаты
    groups: Dict[str, List["RecognizedModule"]] = {}
    for m in modules:
        key = f"{m.type}_{m.width}_{m.depth}_{m.height}"
        groups.setdefault(key, []).append(m)

    result = []
    for key, mods in groups.items():
        if len(mods) == 1:
            result.append(mods[0])
        else:
            total_qty = sum(max(m.quantity, 1) for m in mods)
            merged = RecognizedModule(
                type=mods[0].type,
                width=mods[0].width,
                depth=mods[0].depth,
                height=mods[0].height,
                quantity=total_qty,
                has_glass=any(m.has_glass for m in mods),
                facades=mods[0].facades,
                drawers=mods[0].drawers,
                shelves=max(m.shelves for m in mods),
                is_corner=mods[0].is_corner,
                bbox=mods[0].bbox,
            )
            result.append(merged)
            logger.info(f"Merged {len(mods)}× duplicate {key} → qty={total_qty}")

    return result


class GeminiImageAnalyzer:
    """
    Анализатор изображений чертежей через Vision LLM.

    Провайдеры:
    0. Локальный сервер (Qwen3.8 GGUF, OpenAI-compatible) — если настроен LOCAL_LLM_API_URL
    1. RouterAI.ru Qwen3-VL-235B — основной (bbox + типы модулей)
    2. Z.ai GLM-5V-Turbo — fallback (быстрый, дешёвый)
    """

    # Конфигурация моделей (см. config/models.yaml)
    PRIMARY_MODEL = "qwen/qwen3-vl-235b-a22b-thinking"
    FALLBACK_MODEL = "glm-5v-turbo"

    def __init__(self):
        """Инициализация HTTP клиента."""
        self.zai_key = settings.zai_api_key
        self.openrouter_key = settings.openrouter_api_key
        self.routerai_key = settings.routerai_api_key
        self.routerai_url = settings.routerai_api_url
        self.primary_model = settings.vision_model
        self.api_url = settings.vision_api_url

        # Локальный LLM-сервер (OpenAI-compatible), если настроен
        self.local_llm_url = settings.local_llm_api_url.rstrip("/")
        self.local_llm_key = settings.local_llm_api_key
        self.local_vision_model = settings.local_vision_model

        self.client: Optional[httpx.AsyncClient] = None

        providers = []
        if self.local_llm_url:
            providers.append(f"Local ({self.local_vision_model})")
        providers.append("Z.ai")
        if self.routerai_key:
            providers.append("RouterAI.ru")
        logger.info(
            f"Vision Analyzer: primary={self.primary_model}, "
            f"providers={providers}"
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self.client is None:
            # Локальный 27B-модель генерирует медленно — даём больше времени
            timeout = 900.0 if self.local_llm_url else 180.0
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
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
        model: Optional[str] = None,
    ) -> RecognitionResult:
        """
        Простой модульный анализ (одна модель, без ансамбля и цепочек).

        Используется как fallback, если analyze_page() не дал модулей.
        По умолчанию — локальная модель (если настроена), иначе
        FALLBACK_MODEL (glm-5v-turbo через Z.ai).
        """
        model = model or (self.local_vision_model if self.local_llm_url else self.FALLBACK_MODEL)
        provider = self._detect_provider(model)

        logger.info(f"🔄 Fallback: {model} ({provider})")

        try:
            result = await self._try_analyze(image_path, model, provider)
            result.model_used = model

            if result.modules:
                logger.info(
                    f"✅ Fallback: {len(result.modules)} modules, "
                    f"confidence={result.confidence}"
                )
            else:
                logger.warning(f"Fallback {model} вернул пустой результат")

            return result

        except Exception as e:
            logger.error(f"Fallback {model} error: {type(e).__name__}: {e}")
            return RecognitionResult(
                modules=[],
                confidence="low",
                notes=f"Fallback не удался: {e}",
                model_used=model,
            )

    # -----------------------------------------------------------
    # ФАСАДНЫЙ МЕТОД — основной с 2026-07 (Qwen3-VL-235B-Thinking)
    # -----------------------------------------------------------

    async def analyze_facades(
        self,
        image_path: str | Path,
    ) -> FacadeResult:
        """
        Распознать ВСЕ фасады (дверцы) на чертеже.
        Использует Qwen3-VL-235B-Thinking — точность 93% по фасадам, 98% по петлям.

        Возвращает FacadeResult, который можно сконвертировать в модули
        через facades_to_modules().
        """
        FACADE_MODEL = "qwen/qwen3-vl-235b-a22b-thinking"

        logger.info(f"🎯 Фасадный анализ: {FACADE_MODEL}")

        try:
            result = await self._try_analyze_facades(image_path, FACADE_MODEL, "routerai")
            result.model_used = FACADE_MODEL
            return result
        except Exception as e:
            logger.error(f"Фасадный анализ не удался: {e}")
            return FacadeResult(
                facades=[],
                confidence="low",
                notes=f"Ошибка фасадного анализа: {e}",
            )

    async def _try_analyze_facades(
        self, image_path: str | Path, model: str, provider: str
    ) -> FacadeResult:
        """Одна попытка фасадного распознавания."""
        client = await self._get_client()

        # Предобработка изображения
        image = Image.open(image_path)
        from PIL import ImageEnhance, ImageFilter
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(1.3)
        image = image.filter(ImageFilter.SHARPEN)
        max_size = 1536
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
                {"type": "text", "text": FACADE_PROMPT},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{image_base64}"
                }}
            ]
        }]

        headers = {
            "Authorization": f"Bearer {self._get_api_key(provider)}",
            "Content-Type": "application/json",
        }

        body = {
            "model": model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 8000,
        }

        url = self._get_api_url(provider, model)
        response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()
        response_data = response.json()

        msg = response_data["choices"][0]["message"]
        response_text = msg.get("content", "") or ""
        if not response_text:
            raise ValueError(f"Empty response from {model}")

        logger.info(f"Facade response ({model[:30]}...): {response_text[:300]}...")

        # Парсим JSON
        cleaned_text = self._extract_json(response_text)
        result_data = json.loads(cleaned_text)

        facades = []
        for f_data in result_data.get("facades", []):
            try:
                facades.append(FacadeData(
                    width_mm=int(f_data.get("width_mm", 0)),
                    height_mm=int(f_data.get("height_mm", 716)),
                    zone=f_data.get("zone", "lower"),
                    is_corner=f_data.get("is_corner", False),
                ))
            except (ValueError, TypeError) as e:
                logger.warning(f"Skipping facade: {e}, data={f_data}")

        return FacadeResult(
            facades=facades,
            confidence=result_data.get("confidence", "low"),
            zone_type=result_data.get("zone_type"),
            materials_mentioned=result_data.get("materials", []),
            notes=result_data.get("notes"),
        )

    # -----------------------------------------------------------
    # ФАСАДНЫЙ МЕТОД С МАСШТАБОМ — bbox + Python = точные размеры
    # -----------------------------------------------------------

    async def analyze_facades_scaled(
        self,
        image_path: str | Path,
    ) -> Tuple[List[ScaledFacade], Optional[str], List[str]]:
        """
        Фасадный анализ с вычислением размеров через bbox + масштаб.

        1. Qwen3-VL-235B-Thinking даёт bbox фасадов + общий габарит
        2. Python считает масштаб и переводит проценты в мм

        Returns:
            (facades: List[ScaledFacade], zone_type, materials)
        """

        SCALE_MODEL = "qwen/qwen3-vl-235b-a22b-thinking"

        logger.info(f"📐 Масштабный анализ: {SCALE_MODEL}")

        try:
            client = await self._get_client()

            # Предобработка
            image = Image.open(image_path)
            from PIL import ImageEnhance, ImageFilter
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(1.3)
            image = image.filter(ImageFilter.SHARPEN)
            max_size = 1536
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
                    {"type": "text", "text": SCALE_PROMPT},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    }}
                ]
            }]

            headers = {
                "Authorization": f"Bearer {self.routerai_key}",
                "Content-Type": "application/json",
            }

            body = {
                "model": SCALE_MODEL,
                "messages": messages,
                "temperature": 0.0,
                "max_tokens": 8000,
            }

            url = self._get_api_url("routerai", SCALE_MODEL)
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            response_data = response.json()

            msg = response_data["choices"][0]["message"]
            response_text = msg.get("content", "") or ""

            # Парсим JSON
            cleaned = self._extract_json(response_text)
            data = json.loads(cleaned)

            total_width_mm = data.get("total_width_mm", 0)
            facades_data = data.get("facades", [])
            zone_type = data.get("zone_type")

            logger.info(
                f"📐 Масштаб: габарит={total_width_mm}мм, "
                f"фасадов={len(facades_data)}"
            )

            if total_width_mm <= 0:
                logger.warning(
                    "Габарит не найден — масштабный анализ невозможен, "
                    "переходим к фасадному/модульному методу"
                )
                return [], None, []

            # Считаем размеры через scale_calc
            facades = calculate_scaled_facades(facades_data, total_width_mm)

            return facades, zone_type, data.get("materials", [])

        except Exception as e:
            logger.error(f"Масштабный анализ не удался: {e}")
            return [], None, []

    # -----------------------------------------------------------
    # ЕДИНЫЙ АНАЛИЗ СТРАНИЦЫ (v2 — основной метод с 2026-07-12)
    # Один вызов qwen3-vl-235b: bbox + типы модулей + материалы
    # -----------------------------------------------------------

    async def analyze_page(
        self,
        image_path: str | Path,
    ) -> Tuple[List[RecognizedModule], Optional[str], List[str], str]:
        """
        Единый анализ страницы чертежа.

        1. Qwen3-VL-235B с UNIFIED_PROMPT_V2 (только дверцы + габарит):
           - bbox КАЖДОЙ дверцы в процентах + total_width_mm с размерной линии
           - Python вычисляет точные мм через scale_calc
           - Python группирует дверцы в модули (без помощи модели!)
        2. Если total_width_mm <= 0 → fallback на стандартные размеры

        Returns:
            (modules, zone_type, materials, confidence)
        """
        use_local = bool(self.local_llm_url)
        MODEL = self.local_vision_model if use_local else "qwen/qwen3-vl-235b-a22b-thinking"

        logger.info(f"🎯 Единый анализ: {MODEL} ({'local' if use_local else 'RouterAI'})")

        try:
            client = await self._get_client()

            # Предобработка изображения
            image = Image.open(image_path)
            from PIL import ImageEnhance, ImageFilter
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(1.3)
            image = image.filter(ImageFilter.SHARPEN)
            max_size = 1536
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
                    {"type": "text", "text": UNIFIED_PROMPT_V2},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    }}
                ]
            }]

            headers = {
                "Authorization": f"Bearer {(self.local_llm_key if use_local else self.routerai_key)}",
                "Content-Type": "application/json",
            }

            body = {
                "model": MODEL,
                "messages": messages,
                "temperature": 0.0,
                # Локальная — thinking-модель: запас на длинный JSON
                "max_tokens": 16000 if use_local else 8000,
            }
            if not use_local:
                body["response_format"] = {"type": "json_object"}
            else:
                # Локальный сервер (unsloth-studio): response_format не поддержан —
                # JSON извлекается через _extract_json. Thinking выключен: иначе
                # reasoning съедает бюджет токенов и засоряет ответ рассуждениями.
                body["chat_template_kwargs"] = {"enable_thinking": False}

            url = self._get_api_url("local" if use_local else "routerai", MODEL)
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            response_data = response.json()

            msg = response_data["choices"][0]["message"]
            response_text = msg.get("content", "") or ""

            # Парсим JSON
            cleaned = self._extract_json(response_text)
            data = json.loads(cleaned)

            total_width_mm = data.get("total_width_mm", 0)
            facades_data = data.get("facades", [])
            glass_indices = set(data.get("has_glass_facades", []))
            drawer_indices = set(data.get("drawer_indices", []))
            heights_mm = data.get("heights_mm") or {}
            zone_type = data.get("zone_type")
            materials = data.get("materials", [])
            confidence = data.get("confidence", "medium")

            logger.info(
                f"📐 Единый: габарит={total_width_mm}мм, "
                f"дверей={len(facades_data)}, "
                f"ящиков={len(drawer_indices)}, "
                f"высоты={heights_mm or 'не прочитаны'}, "
                f"confidence={confidence}"
            )

            # Если есть и габарит, и bbox-данные → точные размеры через scale_calc
            scaled_facades = []
            if total_width_mm > 0 and facades_data:
                # Высоты рядов (lower/upper/penal), прочитанные моделью с чертежа,
                # передаём в scale_calc — иначе берутся константы 716/596/2500.
                # Защита от явно абсурдных значений (0/1/9999 от модели).
                def _plausible_height(v):
                    return v if (v is not None and 200 <= int(v) <= 2900) else None

                scaled_facades = calculate_scaled_facades(
                    facades_data,
                    total_width_mm,
                    lower_height_mm=_plausible_height(heights_mm.get("lower")),
                    upper_height_mm=_plausible_height(heights_mm.get("upper")),
                    penal_height_mm=_plausible_height(heights_mm.get("penal")),
                )
                logger.info(
                    f"📏 Масштаб: {len(scaled_facades)} дверей с вычисленными размерами"
                )
            elif total_width_mm <= 0:
                logger.warning("Габарит не найден — вернусь к стандартным размерам")
                return [], zone_type, materials, "low"

            if not scaled_facades:
                logger.warning("Не удалось вычислить размеры — вернусь к стандартным")
                return [], zone_type, materials, "low"

            # Python: создаём модули из фасадов (без помощи модели!)
            modules = self._facades_to_modules(scaled_facades, zone_type, glass_indices, drawer_indices)

            return modules, zone_type, materials, confidence

        except Exception as e:
            logger.error(f"Единый анализ не удался: {type(e).__name__}: {e}")
            return [], None, [], "low"

    def _facades_to_modules(
        self,
        scaled_facades: List,
        zone_type: Optional[str] = None,
        glass_indices: set = None,
        drawer_indices: set = None,
    ) -> List[RecognizedModule]:
        """
        Python-группировка: отдельные дверцы → модули мебели.

        Правила:
        - Каждая дверца = потенциально отдельный модуль
        - Дверцы одной зоны (lower/upper/penal) с одинаковой шириной (±30mm) = один тип модуля
        - Угловая дверца (is_corner) → модуль corner (квадратный)
        - Пенал → всегда отдельный модуль (не группируем!)
        - Дверцы с одинаковой шириной группируются с quantity=N
        - drawer_indices: индексы фасадов с ящиками (1-based)
        """
        if not scaled_facades:
            return []

        rules = get_rules(zone_type)
        glass_indices = glass_indices or set()
        drawer_indices = drawer_indices or set()

        modules = []

        # Группируем по зонам
        by_zone = {}
        for i, sf in enumerate(scaled_facades):
            zone = sf.zone if sf.zone in ("lower", "upper", "penal") else "lower"
            by_zone.setdefault(zone, []).append((i, sf))

        for zone in ["lower", "upper", "penal"]:
            zone_items = by_zone.get(zone, [])
            if not zone_items:
                continue

            # Сортируем по ширине для группировки
            zone_items.sort(key=lambda x: x[1].width_mm)

            i = 0
            while i < len(zone_items):
                idx, sf = zone_items[i]

                # Угловой → отдельный модуль (ТОЛЬКО по явному признаку модели:
                # is_corner=true из JSON). Авто-эвристика «нижний фасад 800–1100мм
                # = угол» УБРАНА — она превращала прямые тумбы/шкафы (две двери
                # по ~900мм) в ложные «угловые модули» с глубиной = ширине.
                is_corner = getattr(sf, 'is_corner', False)

                if is_corner:
                    modules.append(RecognizedModule(
                        type="corner",
                        width=sf.width_mm,
                        depth=sf.width_mm,  # corner: квадратный
                        height=sf.height_mm,
                        quantity=1,
                        is_corner=True,
                        has_glass=(idx + 1) in glass_indices,
                        facades={"count": 1, "type": "doors"},
                        drawers={"count": 1} if (idx + 1) in drawer_indices else None,
                    ))
                    i += 1
                    continue

                # Пенал → ВСЕГДА отдельный модуль (даже если несколько рядом)
                if zone == "penal":
                    modules.append(RecognizedModule(
                        type="penal",
                        width=sf.width_mm,
                        depth=rules.default_depth_lower,  # глубина как у нижних баз
                        height=sf.height_mm,
                        quantity=1,
                        has_glass=(idx + 1) in glass_indices,
                        facades={"count": 1, "type": "doors"},
                    ))
                    i += 1
                    continue

                # Группируем дверцы с одинаковой шириной (±30mm)
                group = [(idx, sf)]
                j = i + 1
                while j < len(zone_items):
                    jdx, jsf = zone_items[j]
                    if abs(jsf.width_mm - sf.width_mm) <= 30:
                        group.append((jdx, jsf))
                        j += 1
                    else:
                        break

                avg_width = sum(g[1].width_mm for g in group) // len(group)
                avg_height = sum(g[1].height_mm for g in group) // len(group)
                any_glass = any((g[0] + 1) in glass_indices for g in group)
                any_drawer = any((g[0] + 1) in drawer_indices for g in group)

                mtype = {"lower": "lower_base", "upper": "upper_base"}.get(zone, "lower_base")
                depth = rules.default_depth_upper if zone == "upper" else rules.default_depth_lower

                modules.append(RecognizedModule(
                    type=mtype,
                    width=avg_width,
                    depth=depth,
                    height=avg_height,
                    quantity=len(group),
                    has_glass=any_glass,
                    facades={"count": 1, "type": "doors"},
                    drawers={"count": 1} if any_drawer else None,
                ))

                i = j

        return modules

    # -----------------------------------------------------------
    # ОДНА ПОПЫТКА (модульный метод)
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
        }
        if provider == "local":
            # Локальный сервер (unsloth-studio): response_format не поддержан —
            # JSON извлекается через _extract_json. Thinking выключен: иначе
            # reasoning съедает бюджет токенов и засоряет ответ рассуждениями.
            body["chat_template_kwargs"] = {"enable_thinking": False}
        else:
            body["response_format"] = {"type": "json_object"}  # гарантирует валидный JSON на выходе

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
        # Локальный сервер — проверка ПЕРВАЯ: имя содержит "qwen",
        # иначе модель ошибочно ушла бы в RouterAI
        if self.local_llm_url and model == self.local_vision_model:
            return "local"
        if "glm" in model.lower() and "z-ai" not in model.lower():
            return "zai"      # GLM без префикса → Z.ai
        if "z-ai" in model.lower():
            return "routerai"  # z-ai/glm-* → RouterAI.ru (агрегатор)
        if "qwen" in model.lower() or "google" in model.lower() or "gemini" in model.lower():
            return "routerai"  # Qwen, Gemini → RouterAI.ru
        return "zai"  # по умолчанию

    def _get_api_key(self, provider: str) -> str:
        """Получить API ключ для провайдера."""
        if provider == "local":
            return self.local_llm_key or ""
        if provider == "zai":
            return self.zai_key or ""
        elif provider == "routerai":
            return self.routerai_key or self.zai_key or ""
        elif provider == "openrouter":
            return self.openrouter_key or self.routerai_key or ""
        return self.zai_key or self.routerai_key or ""

    def _get_api_url(self, provider: str, model: str) -> str:
        """Получить URL эндпоинта."""
        if provider == "local":
            return f"{self.local_llm_url}/chat/completions"
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


def facades_to_modules(
    facades: List[FacadeData],
    zone_type: Optional[str] = None,
) -> List[RecognizedModule]:
    """
    Сконвертировать список фасадов в список модулей для расчёта материалов.

    Эвристика:
    - Фасады с одинаковой зоной и близкой высотой (±50мм) группируются в модули
    - Пеналы: каждый фасад = 1 модуль (высокий)
    - Нижние/верхние: фасады одинаковой высоты = отдельные модули
    - Угловые фасады = corner-модуль
    - Валидация: размеры вне реалистичных диапазонов → стандартные
    """
    # Реалистичные диапазоны (гиганты/лилипуты → стандарт)
    VALID_SIZE = {
        "lower": {"width": (250, 1200), "height": (700, 900), "std_w": 600, "std_h": 716, "depth": 560},
        "upper": {"width": (250, 1200), "height": (500, 800), "std_w": 600, "std_h": 596, "depth": 320},
        "penal": {"width": (250, 1200), "height": (1800, 2800), "std_w": 600, "std_h": 2500, "depth": 560},
    }

    modules = []

    # Группируем по zone
    by_zone = {}
    for f in facades:
        by_zone.setdefault(f.zone, []).append(f)

    for zone, zone_facades in by_zone.items():
        limits = VALID_SIZE.get(zone)
        if not limits:
            continue

        if zone == "penal":
            for f in zone_facades:
                w = f.width_mm if limits["width"][0] <= f.width_mm <= limits["width"][1] else limits["std_w"]
                h = f.height_mm if limits["height"][0] <= f.height_mm <= limits["height"][1] else limits["std_h"]
                modules.append(RecognizedModule(
                    type="penal", width=w, depth=limits["depth"], height=h,
                    quantity=1, facades={"count": 1, "type": "doors"}, shelves=0, is_corner=False,
                ))

        elif any(f.is_corner for f in zone_facades):
            corner_f = next((f for f in zone_facades if f.is_corner), zone_facades[0])
            w = corner_f.width_mm if limits["width"][0] <= corner_f.width_mm <= limits["width"][1] else limits["std_w"]
            h = corner_f.height_mm if limits["height"][0] <= corner_f.height_mm <= limits["height"][1] else limits["std_h"]
            modules.append(RecognizedModule(
                type="corner", width=w, depth=w, height=h,
                quantity=1, facades={"count": 1, "type": "doors"}, shelves=0, is_corner=True,
            ))
            other = [f for f in zone_facades if not f.is_corner]
            for f in other:
                w = f.width_mm if limits["width"][0] <= f.width_mm <= limits["width"][1] else limits["std_w"]
                h = f.height_mm if limits["height"][0] <= f.height_mm <= limits["height"][1] else limits["std_h"]
                mod_type = "lower_base" if zone == "lower" else "upper_base"
                modules.append(RecognizedModule(
                    type=mod_type, width=w, depth=limits["depth"], height=h,
                    quantity=1, facades={"count": 1, "type": "doors"}, shelves=1, is_corner=False,
                ))

        else:
            height_groups = {}
            for f in zone_facades:
                h_key = round(f.height_mm / 50) * 50
                height_groups.setdefault(h_key, []).append(f)

            for h_key, group in height_groups.items():
                mod_type = "lower_base" if zone == "lower" else "upper_base"
                for f in group:
                    w = f.width_mm if limits["width"][0] <= f.width_mm <= limits["width"][1] else limits["std_w"]
                    h = f.height_mm if limits["height"][0] <= f.height_mm <= limits["height"][1] else limits["std_h"]
                    modules.append(RecognizedModule(
                        type=mod_type, width=w, depth=limits["depth"], height=h,
                        quantity=1, facades={"count": 1, "type": "doors"}, shelves=1, is_corner=False,
                    ))

    return modules


# ================================================================
# ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР
# ================================================================

_analyzer: Optional[GeminiImageAnalyzer] = None


def get_analyzer() -> GeminiImageAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = GeminiImageAnalyzer()
    return _analyzer
