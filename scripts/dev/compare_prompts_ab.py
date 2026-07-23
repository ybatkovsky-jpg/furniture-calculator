"""
A/B сравнение промптов: SCALE_PROMPT (старый) vs UNIFIED_PROMPT_V2 (новый).

Прогоняет ОБА промпта на эталонных изображениях через одну модель
(Qwen3-VL-235B, RouterAI) и печатает сравнительную таблицу.

Использование: python scripts/dev/compare_prompts_ab.py
Требует: ключи ROUTERAI_API_KEY / ZAI_API_KEY в .env

Стоимость: ~2 вызова × N изображений. По умолчанию N=3.
"""

import asyncio
import base64
import io
import json
import logging
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path для импорта app.*
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx
from PIL import Image, ImageEnhance, ImageFilter

from app.config import settings
from app.services.image_analyzer import SCALE_PROMPT, UNIFIED_PROMPT_V2
from app.services.scale_calc import calculate_scaled_facades

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

MODEL = "qwen/qwen3-vl-235b-a22b-thinking"
_IMG_DIR = _PROJECT_ROOT / "input_images"
IMAGES = [
    str(_IMG_DIR / "Альбом чертежей Рокоссовского-59-79_page-0003.jpg"),
    str(_IMG_DIR / "Альбом чертежей Рокоссовского-59-79_page-0005.jpg"),
    str(_IMG_DIR / "Альбом чертежей Рокоссовского-59-79_page-0007.jpg"),
]


async def call_model(client: httpx.AsyncClient, image_path: str, prompt: str) -> dict:
    """Один вызов модели, возвращает распарсенный JSON."""
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
        "Authorization": f"Bearer {settings.routerai_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{image_base64}"
                }},
            ],
        }],
        "temperature": 0.0,
        "max_tokens": 8000,
        "response_format": {"type": "json_object"},
    }

    url = settings.routerai_api_url
    response = await client.post(url, headers=headers, json=body)
    response.raise_for_status()
    data = response.json()
    text = data["choices"][0]["message"].get("content", "") or ""
    # Извлекаем JSON
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]
    return json.loads(text)


def summarize(data: dict, label: str) -> dict:
    """Извлечь ключевые метрики из ответа модели."""
    facades = data.get("facades", [])
    total = data.get("total_width_mm", 0)
    drawers = data.get("drawer_indices", [])
    glass = data.get("has_glass_facades", [])
    zone = data.get("zone_type", "?")
    materials = data.get("materials", [])
    conf = data.get("confidence", "?")

    # Считаем масштабные размеры через scale_calc
    scaled = []
    if total > 0 and facades:
        scaled = calculate_scaled_facades(facades, total)
    lower_widths = sum(f.width_mm for f in scaled if f.zone == "lower")

    return {
        "label": label,
        "zone": zone,
        "total_width_mm": total,
        "facades_count": len(facades),
        "drawer_indices": drawers,
        "has_glass_facades": glass,
        "lower_sum_mm": lower_widths,
        "materials": materials[:3],
        "confidence": conf,
    }


async def main():
    print("=" * 80)
    print("A/B СРАВНЕНИЕ ПРОМПТОВ: SCALE_PROMPT vs UNIFIED_PROMPT_V2")
    print(f"Модель: {MODEL}")
    print(f"Изображений: {len(IMAGES)}")
    print("=" * 80)

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
        for img_path in IMAGES:
            p = Path(img_path)
            if not p.exists():
                print(f"\n⚠ Пропуск (нет файла): {p.name}")
                continue

            print(f"\n{'─' * 80}")
            print(f"📷 {p.name}")
            print("─" * 80)

            # A: SCALE_PROMPT
            try:
                data_a = await call_model(client, img_path, SCALE_PROMPT)
                summary_a = summarize(data_a, "SCALE_PROMPT")
            except Exception as e:
                print(f"  ❌ SCALE_PROMPT ошибка: {type(e).__name__}: {e}")
                summary_a = None

            # B: UNIFIED_PROMPT_V2
            try:
                data_b = await call_model(client, img_path, UNIFIED_PROMPT_V2)
                summary_b = summarize(data_b, "V2")
            except Exception as e:
                print(f"  ❌ V2 ошибка: {type(e).__name__}: {e}")
                summary_b = None

            if summary_a and summary_b:
                # Сравнительная таблица
                print(f"\n  {'Метрика':<22} {'SCALE_PROMPT':<28} {'V2':<28}")
                print(f"  {'─'*22} {'─'*28} {'─'*28}")
                print(f"  {'zone':<22} {summary_a['zone']:<28} {summary_b['zone']:<28}")
                print(f"  {'total_width_mm':<22} {summary_a['total_width_mm']:<28} {summary_b['total_width_mm']:<28}")
                print(f"  {'facades_count':<22} {summary_a['facades_count']:<28} {summary_b['facades_count']:<28}")
                print(f"  {'lower_sum_mm':<22} {summary_a['lower_sum_mm']:<28} {summary_b['lower_sum_mm']:<28}")
                print(f"  {'drawer_indices':<22} {str(summary_a['drawer_indices']):<28} {str(summary_b['drawer_indices']):<28}")
                print(f"  {'has_glass_facades':<22} {str(summary_a['has_glass_facades']):<28} {str(summary_b['has_glass_facades']):<28}")
                print(f"  {'materials':<22} {str(summary_a['materials']):<28} {str(summary_b['materials']):<28}")
                print(f"  {'confidence':<22} {summary_a['confidence']:<28} {summary_b['confidence']:<28}")

                # Вывод оценок
                same_total = summary_a["total_width_mm"] == summary_b["total_width_mm"]
                same_count = summary_a["facades_count"] == summary_b["facades_count"]
                print(f"\n  Габарит совпадает: {'✅' if same_total else '⚠️ РАЗЛИЧАЕТСЯ'}")
                print(f"  Кол-во фасадов совпадает: {'✅' if same_count else '⚠️ РАЗЛИЧАЕТСЯ'}")
                if summary_b["drawer_indices"] and not summary_a["drawer_indices"]:
                    print(f"  ✅ V2 нашёл ящики: {summary_b['drawer_indices']} (SCALE не нашёл)")
                if summary_b["has_glass_facades"] and not summary_a["has_glass_facades"]:
                    print(f"  ✅ V2 нашёл стекло: {summary_b['has_glass_facades']} (SCALE не нашёл)")

            # Сохраняем полные ответы для детального разбора
            out_dir = _PROJECT_ROOT / "output" / "ab_comparison"
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = p.stem.replace(" ", "_")
            if summary_a:
                (out_dir / f"{stem}_scale.json").write_text(
                    json.dumps(data_a, ensure_ascii=False, indent=2), encoding="utf-8")
            if summary_b:
                (out_dir / f"{stem}_v2.json").write_text(
                    json.dumps(data_b, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'=' * 80}")
    print("✅ Готово. Полные JSON-ответы сохранены в output/ab_comparison/")
    print("=" * 80)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⛔ Остановлено")
        sys.exit(1)
