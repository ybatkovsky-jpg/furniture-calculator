"""
Массовая валидация UNIFIED_PROMPT_V2 на всех эталонных изображениях.

Прогоняет V2 на всех JPG из input_images/, собирает статистику:
- zone_type валидность (матчится с FURNITURE_DEFAULTS)
- total_width_mm диапазон
- количество фасадов
- drawer_indices / has_glass_facades

Local-first (v2.1): при настроенном LOCAL_LLM_API_URL прогон идёт через
локальную Qwen3.8 (unsloth-studio), иначе — RouterAI qwen3-vl-235b.
Маршрут логируется в каждую строку сводки (поле "route").

Параллельность (S1): страницы обрабатываются конкурентно с лимитом
MAX_CONCURRENCY одновременных API-вызовов (asyncio.Semaphore + asyncio.gather).
Каждая страница изолирована: падение одной не роняет остальные — она получает
{"file": ..., "error": ...}. Уже обработанные (out_file.json существует)
пропускаются: читаются из кеша без вызова API (возобновляемый прогон).

Zone-aware ожидания (A1): для зон, где мебель может легитимно быть без дверей
(кровать/стол/диван — Детская/Спальня/Гостиная/Прихожая), пустой facades=[]
при корректных zone_type и total_width_mm НЕ является ошибкой: страница ✅ с
пометкой "facades=[] OK (no-door furniture)", в сводку пишется
"facades_optional": true. Для Кухни/Ванной/Гардеробной и прочих зон требование
фасадов остаётся строгим.

Использование:
  python scripts/dev/v2_validate_all.py                 # полный прогон (параллельно, до 3 запросов)
  python scripts/dev/v2_validate_all.py --summary-only  # пересчёт сводки из уже лежащих
                                                        # output/v2_validation/*.json — БЕЗ API-вызовов
"""

import argparse
import asyncio
import base64
import io
import json
import logging
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path, чтобы импортировать app.*
# независимо от того, откуда запускается скрипт.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx
from PIL import Image, ImageEnhance, ImageFilter

from app.config import settings
from app.services.image_analyzer import UNIFIED_PROMPT_V2
from app.services.furniture_defaults import FURNITURE_DEFAULTS

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Local-first: локальная Qwen3.8, если настроена; иначе облачный референс.
USE_LOCAL = bool(getattr(settings, "local_llm_api_url", ""))
MODEL = settings.local_vision_model if USE_LOCAL else "qwen/qwen3-vl-235b-a22b-thinking"
API_URL = (settings.local_llm_api_url if USE_LOCAL else settings.routerai_api_url).rstrip("/")
API_KEY = settings.local_llm_api_key if USE_LOCAL else settings.routerai_api_key
ROUTE = "local" if USE_LOCAL else "routerai"
# Локальный сервер (unsloth-studio) отдаёт chat/completions по
# LOCAL_LLM_API_URL + "/chat/completions"; RouterAI URL уже полный.
CHAT_URL = API_URL if API_URL.endswith("/chat/completions") else API_URL + "/chat/completions"
IMAGE_DIR = _PROJECT_ROOT / "input_images"
OUT_DIR = _PROJECT_ROOT / "output" / "v2_validation"

# S1: не более этого числа одновременных вызовов API.
MAX_CONCURRENCY = 3
# A1: зоны, где мебель может легитимно быть без дверей (кровать/стол/диван) —
# там пустой facades[] при корректных zone/total не считается ошибкой.
NO_DOOR_ZONES = {"Детская", "Спальня", "Гостиная", "Прихожая"}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Массовая валидация UNIFIED_PROMPT_V2 (S1: параллельно, A1: zone-aware)")
    parser.add_argument(
        "--summary-only", action="store_true",
        help="пересчитать сводку из уже лежащих output/v2_validation/*.json — без API-вызовов",
    )
    return parser.parse_args(argv)


async def call_v2(client: httpx.AsyncClient, image_path: str) -> dict:
    """Один вызов V2, возвращает распарсенный JSON."""
    image = Image.open(image_path)
    image = ImageEnhance.Contrast(image).enhance(1.3)
    image = image.filter(ImageFilter.SHARPEN)
    max_size = 1536
    if image.width > max_size or image.height > max_size:
        ratio = min(max_size / image.width, max_size / image.height)
        image = image.resize(
            (int(image.width * ratio), int(image.height * ratio)),
            Image.Resampling.LANCZOS,
        )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    image_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": UNIFIED_PROMPT_V2},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{image_base64}"
                }},
            ],
        }],
        "temperature": 0.0,
        # Локальная — thinking-модель: запас на длинный JSON
        "max_tokens": 16000 if USE_LOCAL else 8000,
    }
    if not USE_LOCAL:
        body["response_format"] = {"type": "json_object"}
    else:
        # Локальный сервер (unsloth-studio): response_format не поддержан;
        # thinking выключён — иначе reasoning съедает бюджет токенов.
        body["chat_template_kwargs"] = {"enable_thinking": False}

    response = await client.post(CHAT_URL, headers=headers, json=body)
    response.raise_for_status()
    data = response.json()
    text = data["choices"][0]["message"].get("content", "") or ""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]
    return json.loads(text)


def page_status(zone_ok: bool, total_ok: bool, has_modules: bool,
                facades_optional: bool) -> tuple[str, str]:
    """Статус страницы с zone-aware правилами (A1).

    Требование фасадов снимается только для no-door зон (NO_DOOR_ZONES)
    И при корректном total_width_mm; для всех прочих зон оно строгое.
    Возвращает (статус, пометка) — пометка непуста только для легитимно
    бездверных страниц с facades=[].
    """
    modules_satisfied = has_modules or (facades_optional and total_ok)
    if not (zone_ok and total_ok and modules_satisfied):
        return "⚠️", ""
    if not has_modules and facades_optional:
        return "✅", "facades=[] OK (no-door furniture)"
    return "✅", ""


def evaluate_page(img_name: str, data: dict, valid_zones: set,
                  route=None, model=None) -> tuple[dict, str, str]:
    """Собирает строку сводки из ответа модели.

    route/model добавляются только если переданы (полный прогон); при
    --summary-only они переносятся из прошлой _summary.json, если были.
    """
    zone = data.get("zone_type", "?")
    total = data.get("total_width_mm", 0)
    facades = data.get("facades", [])
    drawers = data.get("drawer_indices", [])
    glass = data.get("has_glass_facades", [])
    conf = data.get("confidence", "?")

    zone_ok = zone in valid_zones
    total_ok = 0 < total < 8000
    has_modules = len(facades) > 0
    facades_optional = zone in NO_DOOR_ZONES  # A1

    status, note = page_status(zone_ok, total_ok, has_modules, facades_optional)

    entry = {
        "file": img_name,
        "zone": zone,
        "zone_ok": zone_ok,
        "total_width_mm": total,
        "total_ok": total_ok,
        "facades_count": len(facades),
        "has_modules": has_modules,
        "facades_optional": facades_optional,
        "drawers": drawers,
        "glass": glass,
        "confidence": conf,
    }
    if route is not None and model is not None:
        # Маршрут/модель ставим первыми полями после file — как в полном прогоне.
        entry = {"file": img_name, "route": route, "model": model, **entry}
    return entry, status, note


def print_result(idx: int, total_pages: int, img_name: str,
                 entry: dict, status: str, note: str) -> None:
    """Печать результата одной завершённой страницы."""
    extra = f" | {note}" if note else ""
    print(
        f"[{idx}/{total_pages}] {img_name} {status} "
        f"zone={entry['zone']} ({'OK' if entry['zone_ok'] else 'НЕВЕРНО'}) | "
        f"total={entry['total_width_mm']} ({'OK' if entry['total_ok'] else 'ВНЕ ДИАПАЗОНА'}) | "
        f"facades={entry['facades_count']} | drawers={entry['drawers']} | "
        f"glass={entry['glass']} | conf={entry['confidence']}{extra}",
        flush=True,
    )


async def process_page(client: httpx.AsyncClient, sem: asyncio.Semaphore,
                       img_path: Path, idx: int, total_pages: int,
                       valid_zones: set) -> dict:
    """Обработка одной страницы (параллельная задача, S1).

    Пропуск уже обработанных: out_file.exists() → читаем JSON из кеша, API не
    зовём (возобновляемость). Любая ошибка страницы изолирована: возвращается
    {"file": ..., "error": ...}, остальные задачи gather не роняет.
    """
    try:
        stem = img_path.stem.replace(" ", "_")
        out_file = OUT_DIR / f"{stem}.json"
        reused = False
        if out_file.exists():
            try:
                data = json.loads(out_file.read_text(encoding="utf-8"))
                reused = True
            except Exception:
                # Повреждённый кеш → перезапрашиваем у API (перезапишется ниже).
                logger.warning("кеш %s повреждён — повторный вызов API", out_file.name)
                data = None
        else:
            data = None

        if data is None:
            async with sem:
                try:
                    data = await call_v2(client, str(img_path))
                except Exception as e:
                    msg = f"{type(e).__name__}: {e}"
                    print(f"[{idx}/{total_pages}] {img_path.name} ❌ Ошибка: {msg}",
                          flush=True)
                    return {"file": img_path.name, "error": msg}
            # Сохраняем полный ответ (только для новых прогонов)
            out_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        entry, status, note = evaluate_page(
            img_path.name, data, valid_zones, ROUTE, MODEL)
        if reused:
            print(f"[{idx}/{total_pages}] {img_path.name} ⏭️  уже обработано — "
                  f"пересчёт из кеша, без API", flush=True)
        print_result(idx, total_pages, img_path.name, entry, status, note)
        return entry
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[{idx}/{total_pages}] {img_path.name} ❌ Ошибка: {msg}", flush=True)
        return {"file": img_path.name, "error": msg}


async def run_parallel(client: httpx.AsyncClient, images: list,
                       valid_zones: set) -> list:
    """S1: параллельный прогон — Semaphore(MAX_CONCURRENCY) + asyncio.gather.

    Порядок результатов совпадает с порядком images (gather сохраняет его).
    """
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = [
        process_page(client, sem, img, i, len(images), valid_zones)
        for i, img in enumerate(images, 1)
    ]
    return await asyncio.gather(*tasks)


def summary_only(images: list, valid_zones: set) -> list:
    """A1/--summary-only: пересчёт сводки из уже лежащих JSON — без сети.

    route/model НЕ штампуются текущим маршрутом окружения: они переносятся из
    прошлой _summary.json, если там были (иначе поле опускается), чтобы
    пересчёт не искажал фактические маршруты сохранённых ответов.
    """
    prev = {}
    prev_path = OUT_DIR / "_summary.json"
    if prev_path.exists():
        try:
            prev_rows = json.loads(prev_path.read_text(encoding="utf-8"))
            prev = {r["file"]: r for r in prev_rows if "file" in r}
        except Exception as e:
            print(f"⚠️ прошлая сводка не читается ({type(e).__name__}: {e}) — "
                  f"пересчёт без переноса route/model", flush=True)

    results = []
    for i, img_path in enumerate(images, 1):
        stem = img_path.stem.replace(" ", "_")
        out_file = OUT_DIR / f"{stem}.json"
        if not out_file.exists():
            print(f"[{i}/{len(images)}] {img_path.name} ⏭️  нет кеша — пропуск",
                  flush=True)
            continue
        try:
            data = json.loads(out_file.read_text(encoding="utf-8"))
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(f"[{i}/{len(images)}] {img_path.name} ❌ повреждён кеш: {msg}",
                  flush=True)
            results.append({"file": img_path.name, "error": msg})
            continue
        prior = prev.get(img_path.name, {})
        route = prior.get("route") if "route" in prior else None
        model = prior.get("model") if "model" in prior else None
        entry, status, note = evaluate_page(
            img_path.name, data, valid_zones, route, model)
        print_result(i, len(images), img_path.name, entry, status, note)
        results.append(entry)
    return results


def print_summary(results: list, valid_zones: set) -> None:
    """Сводка — формат вывода не меняется."""
    print(f"\n{'=' * 80}")
    print("СВОДКА ВАЛИДАЦИИ")
    print("=" * 80)
    ok_results = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]
    print(f"Всего: {len(results)} | Успешно: {len(ok_results)} | Ошибок: {len(errors)}")
    if ok_results:
        zone_ok_count = sum(1 for r in ok_results if r["zone_ok"])
        total_ok_count = sum(1 for r in ok_results if r["total_ok"])
        modules_count = sum(1 for r in ok_results if r["has_modules"])
        drawers_found = sum(1 for r in ok_results if r["drawers"])
        glass_found = sum(1 for r in ok_results if r["glass"])
        print(f"  zone_type валиден: {zone_ok_count}/{len(ok_results)}")
        print(f"  total_width_mm в диапазоне: {total_ok_count}/{len(ok_results)}")
        print(f"  Модули найдены: {modules_count}/{len(ok_results)}")
        print(f"  Ящики найдены: {drawers_found}/{len(ok_results)}")
        print(f"  Стекло найдено: {glass_found}/{len(ok_results)}")
        print(f"\n  Распределение по зонам:")
        from collections import Counter
        zones = Counter(r["zone"] for r in ok_results)
        for z, c in zones.most_common():
            mark = "✅" if z in valid_zones else "❌"
            print(f"    {mark} {z}: {c}")


async def main():
    args = parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    images = sorted(IMAGE_DIR.glob("*.jpg"))
    print("=" * 80)
    if args.summary_only:
        print("ВАЛИДАЦИЯ V2 — пересчёт сводки из output/v2_validation/ (без API)")
    else:
        print(f"ВАЛИДАЦИЯ V2 — {len(images)} изображений")
    print("=" * 80)

    valid_zones = set(FURNITURE_DEFAULTS.keys())
    results = []

    if args.summary_only:
        results = summary_only(images, valid_zones)
    else:
        print(f"Маршрут: {ROUTE} | модель: {MODEL} | параллельность: {MAX_CONCURRENCY}",
              flush=True)
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            results = await run_parallel(client, images, valid_zones)

    # Сводка
    print_summary(results, valid_zones)

    # Сохраняем сводку
    summary_path = OUT_DIR / "_summary.json"
    summary_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n📁 Полные ответы: {OUT_DIR}/")
    print(f"📋 Сводка: {summary_path}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⛔ Остановлено")
        sys.exit(1)
