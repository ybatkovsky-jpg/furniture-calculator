"""
OCR-заземление чисел размерных линий.

Проблема: локальная vision-модель (orcarouter/Qwen3.8-27B-Uncensored-GGUF)
при temperature=0 даёт РАЗНЫЕ значения габарита на один и тот же чертёж
(стр. 0011: 1581↔2581, стр. 0004: 1830↔3000). OCR цифр стабильнее,
поэтому числа с размерных линий читает OCR и они становятся ground truth
для total_width_mm (приоритет над ответом модели).

Источники (по приоритету):
1. Локальный Tesseract (pytesseract + tesseract.exe) — image_to_data с bbox.
2. Облачный GLM-OCR (Z.ai layout_parsing) — по образцу app/services/pdf_parser.py:
   в ответе layout_details содержатся распознанные текстовые элементы
   с bbox_2d, плюс prompt просит JSON-перечисление чисел в md_results.

Все бинарные/сетевые зависимости опциональны: при отсутствии tesseract/
pytesseract И недоступности GLM-OCR модуль возвращает [] и логирует WARNING
один раз — конвейер работает как раньше (габарит из ответа VLM).
"""

import base64
import io
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Диапазон правдоподобных габаритов мебели, мм
MIN_DIM_MM = 200
MAX_DIM_MM = 8000

GLM_OCR_API_URL = "https://api.z.ai/api/paas/v4/layout_parsing"

# Запрос к GLM-OCR: перечислить все числа с положением в долях изображения.
# layout_details даёт точные bbox, prompt-JSON — запасной источник позиций.
GLM_PROMPT = (
    "Перечисли ВСЕ числа на чертеже (размеры с размерных линий и габариты, "
    "кроме номеров страниц и дат). Для каждого числа укажи положение центра "
    "(x, y) и размер области (w, h) в долях изображения от 0 до 1. "
    'Верни ТОЛЬКО JSON: {"numbers": [{"value": 1581, "x": 0.5, "y": 0.85, '
    '"w": 0.12, "h": 0.03}]}'
)

# Один WARNING на процесс: OCR недоступен — работаем как раньше
_warned = False
_tesseract_checked: Optional[bool] = None
_client: Optional[httpx.AsyncClient] = None
# Circuit breaker: после сбоя GLM-OCR не дёргаем сеть 10 минут
# (иначе ReadTimeout по 180 c на каждую страницу при мёртвом API)
_glm_dead_until: float = 0.0


# ================================================================
# ПУБЛИЧНОЕ API
# ================================================================

async def extract_dimension_numbers(image_path: str | Path) -> List[dict]:
    """
    Извлечь числа-кандидаты габаритов с изображения чертежа.

    Возвращает список {"value": int, "x": float, "y": float, "w": float,
    "h": float} в НОРМАЛИЗОВАННЫХ координатах 0..1 от размеров изображения.
    Только числа в диапазоне 200..8000 мм.

    Порядок источников: локальный Tesseract → облачный GLM-OCR.
    При недоступности обоих возвращает [] и логирует WARNING один раз.
    """
    path = Path(image_path)
    if not path.exists():
        logger.warning(f"🔤 OCR: файл не найден: {path}")
        return []

    img_w, img_h = _image_size(path)

    candidates: List[dict] = []
    if _tesseract_available():
        candidates = _extract_tesseract(path, img_w, img_h)
    if not candidates:
        candidates = await _extract_glm(path, img_w, img_h)

    if not candidates:
        _warn_once(
            "🔤 OCR размеров недоступен (нет tesseract/pytesseract и GLM-OCR) — "
            "габарит берётся из ответа VLM, как раньше"
        )
    return candidates


def select_total_width(
    candidates: List[dict],
    vlm_width: Optional[int],
) -> Tuple[Optional[int], float]:
    """
    Выбор числа-габарита из OCR-кандидатов (чистая функция, без сети).

    Правила:
    (а) если vlm_width задан (>0): среди кандидатов в пределах ±10% от
        vlm_width выбирается самый «похожий на цифру размерной линии»
        (наибольшая ширина строки + позиция в середине нижних 2/3
        изображения); confidence 0.70..0.95 обратно пропорциональна
        отклонению от vlm_width.
    (б) если vlm_width отсутствует ИЛИ близкого кандидата нет / он слабый:
        выбирается кандидат с наибольшим x-размахом/центральностью;
        confidence растёт с «силой» кандидата (≥0.7 только для явной
        цифры размерной линии — широкой и центральной в нижних 2/3).

    Returns:
        (значение_мм, confidence 0..1) или (None, 0) если кандидатов нет.
    """
    if not candidates:
        return None, 0.0

    valid: List[dict] = []
    for c in candidates:
        if isinstance(c, dict):
            cc = _as_candidate(c)
            if cc is not None:
                valid.append(cc)
    if not valid:
        return None, 0.0

    has_vlm = isinstance(vlm_width, (int, float)) and vlm_width > 0

    def width_score(c: dict) -> float:
        return min(1.0, c["w"] / 0.12)

    def x_bonus(c: dict) -> float:
        cx = c["x"] + c["w"] / 2.0
        return max(0.0, 1.0 - abs(cx - 0.5) * 2.0)

    def y_bonus(c: dict) -> float:
        cy = c["y"] + c["h"] / 2.0
        return 1.0 if cy >= 1.0 / 3.0 else max(0.0, cy * 3.0)

    def strength(c: dict) -> float:
        """Похожесть на цифру размерной линии: ширина + центральность."""
        return min(1.0, width_score(c) + 0.5 * x_bonus(c) * y_bonus(c))

    if has_vlm:
        near = [c for c in valid if abs(c["value"] - vlm_width) <= vlm_width * 0.10]
        best_near = max(near, key=strength) if near else None
        if best_near is not None and strength(best_near) >= 0.5:
            # (а) близкое к VLM И похожее на цифру размерной линии
            rel = abs(best_near["value"] - vlm_width) / float(vlm_width)
            proximity = max(0.0, 1.0 - rel / 0.10)
            confidence = round(0.70 + 0.25 * proximity, 3)
            return best_near["value"], confidence

    # (б) vlm отсутствует или близкого «сильного» кандидата нет
    best = max(valid, key=strength)
    confidence = round(0.40 + 0.50 * strength(best), 3)
    return best["value"], confidence


def parse_glm_dimensions(
    payload: Any,
    image_width: Optional[int] = None,
    image_height: Optional[int] = None,
) -> List[dict]:
    """
    Устойчивый парсер ответа GLM-OCR (layout_parsing) → кандидаты чисел.

    Источники позиций (по приоритету):
    1. layout_details — распознанные элементы с bbox_2d (настоящие
       OCR-координаты каждого числа).
    2. md_results — JSON-перечисление чисел из prompt (допускается
       markdown-обёртка ```json ... ```), затем простой regex-фолбэк
       по тексту с примерным положением.

    Возвращает НОРМАЛИЗОВАННЫЕ кандидаты {"value", "x", "y", "w", "h"}
    (0..1, value в 200..8000). Чистая функция — тестируется без сети.
    """
    if not isinstance(payload, dict):
        return []

    candidates: List[dict] = []
    layout = payload.get("layout_details")
    if isinstance(layout, list) and layout:
        page_sizes = _page_sizes(payload)
        candidates = _from_layout_details(layout, page_sizes, image_width, image_height)

    if not candidates:
        text = payload.get("md_results") or ""
        candidates = _from_md_text(text, image_width, image_height)

    out: List[dict] = []
    seen = set()
    for c in candidates:
        n = _norm_candidate(
            c.get("value"), c.get("x"), c.get("y"), c.get("w"), c.get("h"),
            image_width, image_height,
        )
        if n is None:
            continue
        key = (n["value"], round(n["x"], 3), round(n["y"], 3))
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


# ================================================================
# ЛОКАЛЬНЫЙ TESSERACT
# ================================================================

def _tesseract_available() -> bool:
    """Tesseract + pytesseract доступны? (проверяется один раз)."""
    global _tesseract_checked
    if _tesseract_checked is None:
        try:
            import pytesseract  # noqa: F401
            pytesseract.get_tesseract_version()
            _tesseract_checked = True
        except Exception:
            _tesseract_checked = False
    return _tesseract_checked


def _extract_tesseract(
    path: Path,
    img_w: Optional[int],
    img_h: Optional[int],
) -> List[dict]:
    """Числа через pytesseract image_to_data (bbox каждого слова)."""
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return []
    try:
        image = Image.open(path)
    except Exception:
        return []

    raw: List[Tuple[int, float, float, float, float]] = []
    seen = set()
    # psm 6 — «строки чисел»; psm 11 — разреженный текст (запасной)
    for psm in ("6", "11"):
        try:
            data = pytesseract.image_to_data(
                image,
                output_type=pytesseract.Output.DICT,
                config=f"--psm {psm} -c tessedit_char_whitelist=0123456789.",
            )
        except Exception:
            continue
        rows = len(data.get("text") or [])
        for i in range(rows):
            text = data["text"][i] or ""
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError, IndexError):
                conf = -1.0
            if conf < 40:
                continue
            try:
                x = float(data["left"][i])
                y = float(data["top"][i])
                w = float(data["width"][i])
                h = float(data["height"][i])
            except (TypeError, ValueError, IndexError):
                continue
            for value in _int_groups(text):
                key = (value, round(x), round(y))
                if key in seen:
                    continue
                seen.add(key)
                raw.append((value, x, y, w, h))

    out: List[dict] = []
    for value, x, y, w, h in raw:
        n = _norm_candidate(value, x, y, w, h, img_w, img_h)
        if n is not None:
            out.append(n)
    return out


# ================================================================
# ОБЛАЧНЫЙ GLM-OCR (Z.ai)
# ================================================================

async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(180.0))
    return _client


async def _extract_glm(
    path: Path,
    img_w: Optional[int],
    img_h: Optional[int],
) -> List[dict]:
    """Числа через GLM-OCR layout_parsing (по образцу pdf_parser)."""
    global _glm_dead_until
    api_key = settings.zai_api_key or settings.openrouter_api_key
    if not api_key:
        _warn_once("🔤 OCR размеров: GLM-OCR недоступен — нет ZAI_API_KEY")
        return []
    if time.monotonic() < _glm_dead_until:
        return []  # недавно упал — не тратим время на повторные попытки

    try:
        client = await _get_client()
        data_url = _to_data_url(path)
        payload = await _glm_request(client, api_key, data_url)
        candidates = parse_glm_dimensions(payload, img_w, img_h)
        if candidates:
            values = [c["value"] for c in candidates[:15]]
            logger.info(f"🔤 OCR (GLM): {len(candidates)} чисел-кандидатов: {values}")
        else:
            logger.info("🔤 OCR (GLM): чисел не найдено")
        return candidates
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = (e.response.text or "")[:160]
        except Exception:
            pass
        _glm_dead_until = time.monotonic() + 600.0
        _warn_once(
            f"🔤 OCR размеров: GLM-OCR HTTP {e.response.status_code}: {detail}"
        )
        return []
    except Exception as e:
        _glm_dead_until = time.monotonic() + 600.0
        _warn_once(f"🔤 OCR размеров: GLM-OCR недоступен ({type(e).__name__}: {e})")
        return []


async def _glm_request(
    client: httpx.AsyncClient,
    api_key: str,
    data_url: str,
) -> dict:
    """POST layout_parsing; если prompt не поддержан (400) — без prompt."""
    for body in (
        {"model": "glm-ocr", "file": data_url, "prompt": GLM_PROMPT},
        {"model": "glm-ocr", "file": data_url},
    ):
        try:
            response = await client.post(
                GLM_OCR_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if "prompt" in body and e.response.status_code == 400:
                continue  # возможно, prompt не поддержан — пробуем без него
            raise
    raise RuntimeError("GLM-OCR request failed")


def _to_data_url(path: Path) -> str:
    """Изображение → data URL. Большие сканы уменьшаются до 1600px:
    GLM-OCR работает заметно быстрее, а bbox в ответе нормализованы
    (0..1), поэтому масштаб не влияет на координаты."""
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif data[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    else:
        ext = path.suffix.lower().lstrip(".")
        mime = "image/" + (ext if ext in ("png", "jpg", "jpeg", "webp") else "jpeg")

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        max_side = max(img.width, img.height)
        if max_side > 1600:
            ratio = 1600 / max_side
            img = img.resize(
                (int(img.width * ratio), int(img.height * ratio)),
                Image.Resampling.LANCZOS,
            )
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            data = buf.getvalue()
            mime = "image/jpeg"
    except Exception:
        pass  # не удалось обработать — шлём оригинал

    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


# ================================================================
# ПАРСИНГ ОТВЕТА GLM-OCR
# ================================================================

def _page_sizes(payload: dict) -> List[Tuple[Optional[int], Optional[int]]]:
    sizes: List[Tuple[Optional[int], Optional[int]]] = []
    data_info = payload.get("data_info") or {}
    for page in data_info.get("pages") or []:
        if isinstance(page, dict):
            sizes.append((page.get("width"), page.get("height")))
    return sizes


def _from_layout_details(
    layout: list,
    page_sizes: List[Tuple[Optional[int], Optional[int]]],
    img_w: Optional[int],
    img_h: Optional[int],
) -> List[dict]:
    """Элементы layout_details: content + bbox_2d каждого числа."""
    out: List[dict] = []
    # один page-список элементов либо список страниц
    pages = [layout] if (layout and isinstance(layout[0], dict)) else layout
    for page_idx, page in enumerate(pages):
        if not isinstance(page, list):
            continue
        page_w, page_h = (
            page_sizes[page_idx] if page_idx < len(page_sizes) else (None, None)
        )
        for el in page:
            if not isinstance(el, dict):
                continue
            content = str(el.get("content") or "")
            bbox = el.get("bbox_2d")
            if not content or not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                continue
            pw = el.get("width") or page_w
            ph = el.get("height") or page_h
            x, y, w, h = _bbox_to_xywh(bbox, pw, ph, img_w, img_h)
            for value in _int_groups(content):
                out.append({"value": value, "x": x, "y": y, "w": w, "h": h})
    return out


def _bbox_to_xywh(
    bbox,
    page_w: Optional[int],
    page_h: Optional[int],
    img_w: Optional[int],
    img_h: Optional[int],
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """bbox [x0,y0,x1,y1] (или [x,y,w,h]) → (x, y, w, h) в долях 0..1."""
    try:
        a, b, c, d = (float(v) for v in bbox[:4])
    except (TypeError, ValueError):
        return None, None, None, None

    def to01(v: float, dim: Optional[int]) -> Optional[float]:
        if dim and v > 1.0:
            v = v / dim
        elif v > 1.0 and v <= 100.0:
            v = v / 100.0  # проценты
        return max(0.0, min(1.0, v))

    a, b, c, d = to01(a, page_w or img_w), to01(b, page_h or img_h), \
        to01(c, page_w or img_w), to01(d, page_h or img_h)
    if c > a and d > b:
        return a, b, c - a, d - b
    # интерпретация [x, y, w, h]
    return a, b, min(1.0, c), min(1.0, d)


def _from_md_text(
    text: str,
    img_w: Optional[int],
    img_h: Optional[int],
) -> List[dict]:
    """Числа из md_results: JSON из prompt (устойчиво) → regex-фолбэк."""
    out: List[dict] = []
    obj = _extract_json_object(text)
    if obj is not None:
        entries = _json_number_entries(obj)
        if entries:
            for e in entries:
                value = _to_int(_first_key(e, ("value", "number", "num", "v", "digit")))
                if value is None:
                    continue
                out.append({
                    "value": value,
                    "x": _to_float(_first_key(e, ("x", "cx", "center_x"))),
                    "y": _to_float(_first_key(e, ("y", "cy", "center_y"))),
                    "w": _to_float(_first_key(e, ("w", "width"))),
                    "h": _to_float(_first_key(e, ("h", "height"))),
                })
            return out
    # фолбэк: все числа из текста без точных позиций (примерное положение)
    for value in _int_groups(text):
        out.append({"value": value, "x": None, "y": None, "w": None, "h": None})
    return out


def _extract_json_object(text: str) -> Optional[Any]:
    """Устойчивое извлечение JSON из ответа модели (markdown-обёртка и проза)."""
    if not text:
        return None
    t = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = t.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(t)):
            ch = t[i]
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except Exception:
                        break
        end = t.rfind(close_ch)
        if end > start:
            try:
                return json.loads(t[start:end + 1])
            except Exception:
                pass
    return None


_NUMBER_LIST_KEYS = (
    "numbers", "items", "results", "data", "nums", "values",
    "dimensions", "entries", "dimension_values",
)


def _json_number_entries(obj: Any) -> List[dict]:
    """Найти список записей чисел в произвольном JSON-объекте."""
    if isinstance(obj, list):
        return [e for e in obj if isinstance(e, dict)]
    if not isinstance(obj, dict):
        return []
    for key in _NUMBER_LIST_KEYS:
        val = obj.get(key)
        if isinstance(val, list):
            entries = [e for e in val if isinstance(e, dict)]
            if entries:
                return entries
    for val in obj.values():
        if isinstance(val, dict):
            entries = _json_number_entries(val)
            if entries:
                return entries
    if any(k in obj for k in ("value", "number", "num")):
        return [obj]
    return []


# ================================================================
# УТИЛИТЫ
# ================================================================

def _image_size(path: Path) -> Tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image
    except Exception:
        return None, None
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return None, None


def _as_candidate(c: dict) -> Optional[dict]:
    """Нормализация записи кандидата для select_total_width."""
    v = _to_int(c.get("value"))
    if v is None or not (MIN_DIM_MM <= v <= MAX_DIM_MM):
        return None

    def f(key: str, default: float) -> float:
        val = _to_float(c.get(key))
        return default if val is None else val

    return {
        "value": v,
        "x": f("x", 0.5),
        "y": f("y", 0.66),
        "w": f("w", 0.0),
        "h": f("h", 0.0),
    }


def _norm_candidate(
    value: Any,
    x: Any,
    y: Any,
    w: Any,
    h: Any,
    img_w: Optional[int],
    img_h: Optional[int],
) -> Optional[dict]:
    """Кандидат → нормализованный dict (0..1) с фильтром 200..8000."""
    v = _to_int(value)
    if v is None or not (MIN_DIM_MM <= v <= MAX_DIM_MM):
        return None

    def to01(val: Any, dim: Optional[int], default: float) -> float:
        f = _to_float(val)
        if f is None:
            return default
        if f > 1.0:
            if dim:
                f = f / dim
            elif f <= 100.0:
                f = f / 100.0  # проценты
        return max(0.0, min(1.0, f))

    return {
        "value": v,
        "x": to01(x, img_w, 0.5),
        "y": to01(y, img_h, 0.66),
        "w": to01(w, img_w, 0.08),
        "h": to01(h, img_h, 0.03),
    }


def _int_groups(text: str) -> List[int]:
    """Целые числа из текста: 3–5 цифр целиком или группы по 3–4 цифры."""
    if not text:
        return []
    clean = re.sub(r"\s+", "", text)
    if clean.isdigit() and 3 <= len(clean) <= 5:
        return [int(clean)]
    return [int(m.group()) for m in re.finditer(r"\d{3,4}", text)]


def _to_int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        s = re.sub(r"[^\d]", "", v)  # "1581 мм" / "1 581" → 1581
        if s:
            try:
                return int(s)
            except ValueError:
                return None
    return None


def _to_float(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:[.,]\d+)?", v)
        if m:
            try:
                return float(m.group().replace(",", "."))
            except ValueError:
                return None
    return None


def _first_key(d: dict, keys) -> Optional[Any]:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _warn_once(message: str) -> None:
    """WARNING один раз за процесс — конвейер работает как раньше."""
    global _warned
    if not _warned:
        _warned = True
        logger.warning(message)
