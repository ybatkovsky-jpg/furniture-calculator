"""
Кэш признания vision-модели (протокол стабильности, часть 1 — «кэш признания»).

Ключ кэша — картинка + промпт:
    data/recognition_cache/{sha256(image)[:16]}_{sha256(prompt)[:8]}.json

Структура записи:
    {
        "image_key":      str,   # sha256 содержимого картинки, первые 16 hex
        "prompt_version": str,   # sha256 текста промпта, первые 8 hex
        "data":           {...}, # РЕЗУЛЬТАТ признания (не сырой ответ модели)
        "approved":       bool,  # подтверждён ли результат (scale_ok/consensus)
        "attempts":       int,   # число реальных vision-вызовов, потраченных на результат
        "saved_at":       str,   # ISO timestamp (UTC)
    }

data хранит результат признания:
    {"modules": [dict'ы RecognizedModule], "zone_type", "materials",
     "confidence", "total_width_mm", "scale_ok", "door_count", "notes"?}

Повреждённый JSON трактуется как промах (load → None) и перезаписывается
при следующем save(). Каталог data/recognition_cache/ уже внесён в .gitignore.
"""

import dataclasses
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Каталог кэша: <repo>/data/recognition_cache/
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "recognition_cache"


def image_key(image_path) -> str:
    """Первые 16 hex-цифр sha256 СОДЕРЖИМОГО файла картинки."""
    h = hashlib.sha256()
    with open(image_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def prompt_version(prompt_text: str) -> str:
    """Первые 8 hex-цифр sha256 текста промпта."""
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:8]


def _cache_path(image_path, prompt_text) -> Path:
    return CACHE_DIR / f"{image_key(image_path)}_{prompt_version(prompt_text)}.json"


def load(image_path, prompt_text) -> Optional[dict]:
    """Прочитать запись кэша.

    Промах (файла нет) или повреждённый JSON → None: трактуем как промах,
    следующая запись save() перезапишет файл.
    """
    path = _cache_path(image_path, prompt_text)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("корень кэша — не объект")
        return data
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("Повреждённый кэш %s (%s) — трактуем как промах", path.name, exc)
        return None


def save(image_path, prompt_text, data: dict, approved: bool, attempts: int) -> Path:
    """Записать результат признания (атомарно: .tmp → replace)."""
    ik = image_key(image_path)
    pv = prompt_version(prompt_text)
    payload = {
        "image_key": ik,
        "prompt_version": pv,
        "data": data,
        "approved": bool(approved),
        "attempts": int(attempts),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{ik}_{pv}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)
    return path


def approve(image_path, prompt_text) -> Optional[dict]:
    """Пометить существующую запись кэша approved=True."""
    path = _cache_path(image_path, prompt_text)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("корень кэша — не объект")
        data["approved"] = True
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return data
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("Не удалось подтвердить кэш %s: %s", path.name, exc)
        return None


# ════════════════════════════════════════════════════════════════
# СЕРИАЛИЗАЦИЯ RecognizedModule ↔ dict
# ════════════════════════════════════════════════════════════════

def module_to_dict(module) -> dict:
    """RecognizedModule (или уже dict) → сериализуемый dict.

    dataclasses.asdict: facades/drawers — обычные dict'ы, bbox — обычный dict.
    """
    if isinstance(module, dict):
        return dict(module)
    return dataclasses.asdict(module)


def dict_to_module(data: dict):
    """dict → RecognizedModule (лишние ключи отбрасываются).

    Импорт image_analyzer ленивый: модуль тяжёлый (httpx/PIL),
    а кэш должен оставаться лёгким для тестов без сети.
    """
    from app.services.image_analyzer import RecognizedModule

    if not isinstance(data, dict):
        raise ValueError(f"Данные модуля — не объект: {type(data).__name__}")
    fields = {f.name for f in dataclasses.fields(RecognizedModule)}
    payload = {k: v for k, v in data.items() if k in fields}
    required = {"type", "width", "depth", "height"}
    if not required <= set(payload):
        raise ValueError(
            f"В данных модуля нет обязательных полей: {sorted(required - set(payload))}"
        )
    return RecognizedModule(**payload)


def modules_to_dicts(modules: List[Any]) -> List[dict]:
    """Список RecognizedModule → список dict'ов для data кэша."""
    return [module_to_dict(m) for m in modules]


def dicts_to_modules(items: List[dict]) -> List[Any]:
    """Список dict'ов из кэша → список RecognizedModule.

    Битые элементы пропускаются с предупреждением (кэш не должен ронять конвейер).
    """
    result = []
    for it in items or []:
        try:
            result.append(dict_to_module(it))
        except (ValueError, TypeError) as exc:
            logger.warning("Пропущен модуль из кэша: %s", exc)
    return result
