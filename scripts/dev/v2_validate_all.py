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

Использование: python scripts/dev/v2_validate_all.py
Стоимость: ~12 вызовов (по 1 на изображение).
"""

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
IMAGE_DIR = _PROJECT_ROOT / "input_images"
OUT_DIR = _PROJECT_ROOT / "output" / "v2_validation"


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

    response = await client.post(API_URL, headers=headers, json=body)
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


async def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    images = sorted(IMAGE_DIR.glob("*.jpg"))
    print("=" * 80)
    print(f"ВАЛИДАЦИЯ V2 — {len(images)} изображений")
    print("=" * 80)

    valid_zones = set(FURNITURE_DEFAULTS.keys())
    results = []

    print(f"Маршрут: {ROUTE} | модель: {MODEL}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        for i, img_path in enumerate(images, 1):
            print(f"\n[{i}/{len(images)}] {img_path.name}", flush=True)

            # Пропуск уже обработанных (для возобновляемого прогона)
            stem = img_path.stem.replace(" ", "_")
            out_file = OUT_DIR / f"{stem}.json"
            if out_file.exists():
                print(f"  ⏭️  уже обработано — пропуск", flush=True)
                data = json.loads(out_file.read_text(encoding="utf-8"))
            else:
                try:
                    data = await call_v2(client, str(img_path))
                except Exception as e:
                    print(f"  ❌ Ошибка: {type(e).__name__}: {e}", flush=True)
                    results.append({"file": img_path.name, "error": str(e)})
                    continue
                # Сохраняем полный ответ (только для новых прогонов)
                out_file.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            zone = data.get("zone_type", "?")
            total = data.get("total_width_mm", 0)
            facades = data.get("facades", [])
            drawers = data.get("drawer_indices", [])
            glass = data.get("has_glass_facades", [])
            conf = data.get("confidence", "?")

            zone_ok = zone in valid_zones
            total_ok = 0 < total < 8000
            has_modules = len(facades) > 0

            status = "✅" if (zone_ok and total_ok and has_modules) else "⚠️"
            print(f"  {status} zone={zone} ({'OK' if zone_ok else 'НЕВЕРНО'}) | "
                  f"total={total} ({'OK' if total_ok else 'ВНЕ ДИАПАЗОНА'}) | "
                  f"facades={len(facades)} | drawers={drawers} | glass={glass} | conf={conf}",
                  flush=True)

            results.append({
                "file": img_path.name,
                "route": ROUTE,
                "model": MODEL,
                "zone": zone,
                "zone_ok": zone_ok,
                "total_width_mm": total,
                "total_ok": total_ok,
                "facades_count": len(facades),
                "has_modules": has_modules,
                "drawers": drawers,
                "glass": glass,
                "confidence": conf,
            })

    # Сводка
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
