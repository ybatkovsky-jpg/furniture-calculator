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
5. Если точный размер не читается — стандартный (низ: 560×820, верх: 320×720, пенал: 560×2500).
6. ВСЕ размеры в мм, целыми числами. Никаких "см" или "м".
7. Угловой модуль — всегда is_corner=true, width=depth. Глубина угла НЕ бывает 320мм.
8. Пеналы считай отдельно — они не входят в группу нижних/верхних баз.
9. Если видишь «×3» или «3 шт» — quantity=3.

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

ПРАВИЛА:
- Каждая видимая дверца = ОДИН фасад
- Планки-заполнители — НЕ фасады, игнорируй
- Проценты оценивай визуально, не нужно считать пиксели

ОПРЕДЕЛИ: zone_type, materials, confidence

Верни ТОЛЬКО JSON:
{"zone_type":"...","materials":[],"total_width_mm":3000,
 "facades":[{"zone":"lower","bbox_x_pct":5,"bbox_w_pct":20},...],
 "confidence":"...","notes":""}"""

# ЕДИНЫЙ ПРОМПТ v2 (2026-07-12)
# Объединяет bbox-проценты + типы модулей + материалы в одном запросе.
# Модель читает ОДНО число (total_width_mm) с размерной линии,
# Python вычисляет точные мм через scale_calc.
# ================================================================

UNIFIED_PROMPT_V2 = """Ты — конструктор-технолог мебельной фабрики. Проанализируй чертёж корпусной мебели и верни структурированный JSON.

===== ЗАДАЧА 1: РАЗМЕРНАЯ ЛИНИЯ (total_width_mm) =====
Найди на чертеже размерную линию над рядом нижних шкафов. Это горизонтальная линия со СТРЕЛКАМИ на концах (← →) и одним числом. Она охватывает ВСЕ нижние модули от левого края до правого.

ПРОВЕРЬ СЕБЯ:
- Число должно быть >1000мм и <8000мм
- Если число <1000 — это размер отдельного шкафа, ищи линию над ВСЕМ рядом
- Если видишь несколько размерных линий — бери ту, что ДЛИННЕЕ всех
- Типичные значения: 1800, 2400, 3000, 3600, 4200
- Если размерная линия не читается — укажи total_width_mm=0

===== ЗАДАЧА 2: BBOX ФАСАДОВ (facades) =====
Для КАЖДОГО видимого фасада (дверцы) укажи:
- zone: "lower" | "upper" | "penal"
- bbox_x_pct: позиция ЛЕВОГО края в % от ширины ИЗОБРАЖЕНИЯ (0-100)
- bbox_w_pct: ШИРИНА фасада в % от ширины ИЗОБРАЖЕНИЯ (0-100)
- is_corner: true ТОЛЬКО для углового фасада (обычно шире остальных)

ВАЖНО для bbox:
- Оценивай проценты с точностью до 1% (не округляй до 5% или 10%)
- Сумма bbox_w_pct всех НИЖНИХ фасадов ≈ их реальной доле на изображении
- Пеналы НЕ включай в сумму нижних (они отдельно, с краю)
- Планки-заполнители (40-80мм) НЕ учитывай — это не фасады

===== ЗАДАЧА 3: МОДУЛИ (modules) =====
Сгруппируй фасады в модули. ОДИН физический корпус = ОДИН модуль.

ТИПЫ МОДУЛЕЙ:
- lower_base: на полу, H=700-900, D=500-600
- upper_base: навесной, H=600-1000, D=280-350
- penal: высокий шкаф H=1800-2800, всегда С КРАЮ
- corner: УГЛОВОЙ, ВСЕГДА квадратный width=depth

УСЛОВНЫЕ ОБОЗНАЧЕНИЯ:
★ Пунктирный треугольник на дверце = направление открывания. Это одна дверца.
★ Горизонтальная линия внутри нижней базы = ящики. Укажи drawers.count.
★ Вертикальная линия ТОЛЬКО в зоне фасада = стык двух дверей. facades.count=2, ОДИН модуль.
★ Вертикальная линия через ВЕСЬ корпус до пола = граница РАЗНЫХ модулей.
★ Штриховка/сетка на фасаде = стекло → has_glass=true.

ЗАЩИТА ОТ ОШИБОК (если размер не читается — бери СТАНДАРТ):
| Параметр          | Мин   | Макс  | Стандарт |
|-------------------|-------|-------|----------|
| Ширина модуля     | 250   | 1200  | 600      |
| Глубина lower     | 500   | 600   | 560      |
| Глубина upper     | 280   | 350   | 320      |
| Высота lower_base | 700   | 900   | 820      |
| Высота upper_base | 600   | 1000  | 720      |
| Высота penal      | 1800  | 2800  | 2500     |
| total_width_mm    | 1200  | 6000  | 0=не найден |
| Модуль уже 150мм  | —     | —     | Это дверца, не модуль |
| Угловой W≠D       | —     | —     | Это НЕ угловой → lower_base |

===== ПРИМЕР 1: прямая кухня (3 нижних + 2 верхних) =====
{"zone_type":"Кухня","materials":["EGGER H1379"],"total_width_mm":3000,
 "facades":[
   {"zone":"lower","bbox_x_pct":5,"bbox_w_pct":20,"is_corner":false},
   {"zone":"lower","bbox_x_pct":25,"bbox_w_pct":20,"is_corner":false},
   {"zone":"lower","bbox_x_pct":45,"bbox_w_pct":20,"is_corner":false},
   {"zone":"upper","bbox_x_pct":5,"bbox_w_pct":20,"is_corner":false},
   {"zone":"upper","bbox_x_pct":25,"bbox_w_pct":20,"is_corner":false}
 ],
 "modules":[
   {"type":"lower_base","width":600,"depth":560,"height":820,"quantity":3,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":1,"is_corner":false},
   {"type":"upper_base","width":600,"depth":320,"height":720,"quantity":2,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":1,"is_corner":false}
 ],
 "confidence":"high","notes":""}

===== ПРИМЕР 2: угловая кухня с пеналом =====
{"zone_type":"Кухня","materials":["EGGER H3158","МДФ матовый"],"total_width_mm":3300,
 "facades":[
   {"zone":"lower","bbox_x_pct":3,"bbox_w_pct":27,"is_corner":true},
   {"zone":"lower","bbox_x_pct":30,"bbox_w_pct":18,"is_corner":false},
   {"zone":"lower","bbox_x_pct":48,"bbox_w_pct":18,"is_corner":false},
   {"zone":"upper","bbox_x_pct":30,"bbox_w_pct":18,"is_corner":false},
   {"zone":"upper","bbox_x_pct":48,"bbox_w_pct":18,"is_corner":false},
   {"zone":"penal","bbox_x_pct":70,"bbox_w_pct":18,"is_corner":false}
 ],
 "modules":[
   {"type":"corner","width":900,"depth":900,"height":820,"quantity":1,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":0,"is_corner":true},
   {"type":"lower_base","width":600,"depth":560,"height":820,"quantity":2,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":1},"shelves":1,"is_corner":false},
   {"type":"upper_base","width":600,"depth":320,"height":720,"quantity":2,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":1,"is_corner":false},
   {"type":"penal","width":600,"depth":560,"height":2500,"quantity":1,"has_glass":false,"facades":{"count":1,"type":"doors"},"drawers":{"count":0},"shelves":0,"is_corner":false}
 ],
 "confidence":"high","notes":""}

ОПРЕДЕЛИ ЗОНУ (строго одно из): Кухня, Гостиная, Спальня, Детская, Прихожая, Ванная, Гардеробная, Кабинет, Балкон, Столовая, Постирочная.

Верни ТОЛЬКО валидный JSON с полями: zone_type, materials, total_width_mm, facades, modules, confidence, notes.

confidence: "high" если размерная линия прочитана; "medium" если часть размеров оценена; "low" если много предположений или чертёж нестандартный."""


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
        model: Optional[str] = None,
    ) -> RecognitionResult:
        """
        Простой модульный анализ (одна модель, без ансамбля и цепочек).

        Используется как fallback, если analyze_page() не дал модулей.
        По умолчанию — FALLBACK_MODEL (glm-5v-turbo через Z.ai).
        """
        model = model or self.FALLBACK_MODEL
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

        1. Qwen3-VL-235B с UNIFIED_PROMPT_V2:
           - bbox фасадов в процентах + total_width_mm с размерной линии
           - типы модулей, фасады, ящики, стекло, материалы
        2. Если total_width_mm > 0 → scale_calc → точные мм
        3. Слияние: точные размеры из bbox + метаданные из modules

        Returns:
            (modules, zone_type, materials, confidence)
        """
        MODEL = "qwen/qwen3-vl-235b-a22b-thinking"

        logger.info(f"🎯 Единый анализ: {MODEL}")

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
                "Authorization": f"Bearer {self.routerai_key}",
                "Content-Type": "application/json",
            }

            body = {
                "model": MODEL,
                "messages": messages,
                "temperature": 0.0,
                "max_tokens": 8000,
                "response_format": {"type": "json_object"},
            }

            url = self._get_api_url("routerai", MODEL)
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
            modules_data = data.get("modules", [])
            zone_type = data.get("zone_type")
            materials = data.get("materials", [])
            confidence = data.get("confidence", "medium")

            logger.info(
                f"📐 Единый: габарит={total_width_mm}мм, "
                f"фасадов={len(facades_data)}, модулей={len(modules_data)}, "
                f"confidence={confidence}"
            )

            # Если есть и габарит, и bbox-данные → точные размеры через scale_calc
            scaled_facades = []
            if total_width_mm > 0 and facades_data:
                scaled_facades = calculate_scaled_facades(facades_data, total_width_mm)
                logger.info(
                    f"📏 Масштаб: {len(scaled_facades)} фасадов с вычисленными размерами"
                )
            elif total_width_mm <= 0:
                logger.warning("Габарит не найден — используем стандартные размеры из modules")

            # Собираем RecognizedModule
            modules = self._build_modules(
                modules_data=modules_data,
                scaled_facades=scaled_facades,
                facades_data=facades_data,
                zone_type=zone_type,
            )

            return modules, zone_type, materials, confidence

        except Exception as e:
            logger.error(f"Единый анализ не удался: {type(e).__name__}: {e}")
            return [], None, [], "low"

    def _build_modules(
        self,
        modules_data: List[Dict],
        scaled_facades: List,
        facades_data: List[Dict],
        zone_type: Optional[str] = None,
    ) -> List[RecognizedModule]:
        """
        Собрать RecognizedModule из ответа модели.

        Приоритет размеров:
        1. bbox-вычисленные (scaled_facades) — самые точные
        2. Из modules (прямое чтение моделью) — если нет bbox
        3. Стандартные из FURNITURE_DEFAULTS — если ни один источник не дал размеров
        """
        modules = []

        # Правила для этого типа помещения
        rules = get_rules(zone_type)

        # Индекс bbox-размеров по zone
        scaled_by_zone = {}
        for sf in scaled_facades:
            scaled_by_zone.setdefault(sf.zone, []).append(sf)

        # Стандартные размеры из правил
        default_dims = {
            "lower":  {"depth": rules.default_depth_lower, "height": rules.default_height_lower},
            "upper":  {"depth": rules.default_depth_upper, "height": rules.default_height_upper},
            "penal":  {"depth": rules.default_depth_lower, "height": rules.default_height_penal},
            "corner": {"depth": 900, "height": rules.default_height_corner},
            "wardrobe": {"depth": rules.default_depth_wardrobe, "height": rules.default_height_lower},
            "tall_cabinet": {"depth": rules.default_depth_tall, "height": rules.default_height_lower},
            "shelf_unit": {"depth": rules.default_depth_shelf, "height": rules.default_height_lower},
            "vanity": {"depth": rules.default_depth_vanity, "height": rules.default_height_lower},
            "drawer_unit": {"depth": rules.default_depth_lower, "height": rules.default_height_lower},
            "open_unit": {"depth": rules.default_depth_shelf, "height": rules.default_height_lower},
        }

        # Собираем модули — метаданные из modules_data, размеры из bbox
        zone_idx = {"lower": 0, "upper": 0, "penal": 0, "corner": 0}

        for md in modules_data:
            try:
                m_type = md.get("type", "lower_base")
                zone = m_type if m_type in ("corner",) else (
                    "penal" if m_type == "penal" else
                    "upper" if m_type == "upper_base" else "lower"
                )

                # Размеры: приоритет у bbox, иначе из modules, иначе из FURNITURE_DEFAULTS
                defaults = default_dims.get(m_type, default_dims["lower"])

                width = int(md.get("width", 0))
                depth = int(md.get("depth", 0)) or defaults["depth"]
                height = int(md.get("height", 0)) or defaults["height"]
                height = int(md.get("height", 0)) or defaults["height"]

                # Если есть bbox-размеры для этой zone — используем их
                sf_list = scaled_by_zone.get(zone, [])
                idx = zone_idx.get(zone, 0)
                if sf_list and idx < len(sf_list):
                    sf = sf_list[idx]
                    width = sf.width_mm
                    if sf.height_mm and sf.height_mm > 0:
                        height = sf.height_mm
                    zone_idx[zone] = idx + 1
                    logger.debug(
                        f"  {m_type}: bbox {width}×{height} (вместо {md.get('width',0)}×{md.get('height',0)})"
                    )
                elif width <= 0:
                    width = defaults.get("std_w", 600)

                # Валидация
                if width < 250 or width > 1200:
                    width = 600

                is_corner = (
                    m_type == "corner"
                    or md.get("is_corner", False)
                    or (m_type == "lower_base" and width == depth and width in (600, 900, 1000))
                )

                module = RecognizedModule(
                    type="corner" if is_corner else m_type,
                    width=width,
                    depth=depth if not is_corner else width,  # corner: depth=width
                    height=height,
                    quantity=md.get("quantity", 1),
                    has_glass=md.get("has_glass", False),
                    facades=md.get("facades"),
                    drawers=md.get("drawers"),
                    shelves=md.get("shelves", 0),
                    is_corner=is_corner,
                )

                # Валидация размеров
                is_valid, reason = _validate_module(module)
                if not is_valid:
                    logger.warning(f"⚠️ Пропущен модуль: {reason}, data={md}")
                    continue

                modules.append(module)

            except Exception as e:
                logger.warning(f"Ошибка сборки модуля: {e}, data={md}")
                continue

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
