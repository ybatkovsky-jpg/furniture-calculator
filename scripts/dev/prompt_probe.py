"""
Prompt-probe: прогоняет заданный текст промпта (по умолчанию prompts/unified.txt)
против локальной vision-модели на одном изображении (по умолчанию — стр. 0008,
кровать в детской) и печатает сырой ответ модели + разобранный JSON в UTF-8.

Использование:
    python scripts/dev/prompt_probe.py
    python scripts/dev/prompt_probe.py --prompt <file.txt> --image <file.jpg> [--attempt N]

Ожидание для стр. 0008 (кровать): zone_type=Детская, facades=[], total_width_mm≈2200.
"""

import argparse
import base64
import io
import json
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageEnhance, ImageFilter

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Локальный vision-эндпоинт (unsloth-studio)
API_URL = "http://192.168.1.133:8888/v1/chat/completions"
API_KEY = "sk-unsloth-c10db5079f5c4a4a99025ea350ef28c6"
MODEL = "orcarouter/Qwen3.8-27B-Uncensored-GGUF"
MAX_TOKENS = 1600


def _load_prompt(path: Path) -> str:
    """Тот же канон, что _load_unified_prompt(): файл .strip()."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"Пустой промпт: {path}")
    return text


def _image_to_base64(image_path: Path) -> str:
    """Предобработка как в v2_validate_all: контраст + резкость + кап 1536px."""
    img = Image.open(image_path)
    img = ImageEnhance.Contrast(img).enhance(1.3)
    img = img.filter(ImageFilter.SHARPEN)
    max_size = 1536
    if img.width > max_size or img.height > max_size:
        ratio = min(max_size / img.width, max_size / img.height)
        img = img.resize(
            (int(img.width * ratio), int(img.height * ratio)),
            Image.Resampling.LANCZOS,
        )
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _extract_json(text: str) -> str:
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return text[start:end]
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=Path,
                        default=_PROJECT_ROOT / "prompts" / "unified.txt")
    parser.add_argument("--image", type=Path,
                        default=_PROJECT_ROOT / "input_images"
                        / "Альбом чертежей Рокоссовского-59-79_page-0008.jpg")
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    prompt_text = _load_prompt(args.prompt.resolve())
    print(f"=== PROMPT PROBE (попытка {args.attempt}) ===")
    print(f"prompt: {args.prompt} ({len(prompt_text)} симв.)")
    print(f"image : {args.image}")

    image_b64 = _image_to_base64(args.image.resolve())

    body = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_text},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{image_b64}"
                }},
            ],
        }],
        "temperature": 0.0,
        "max_tokens": MAX_TOKENS,
        # Локальная модель без thinking: обязательный параметр сервера.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=httpx.Timeout(300.0)) as client:
        resp = client.post(API_URL, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    content = (data["choices"][0]["message"].get("content") or "").strip()
    print("\n--- СЫРОЙ ОТВЕТ МОДЕЛИ ---")
    print(content)
    print("--- КОНЕЦ ОТВЕТА ---")

    try:
        parsed = json.loads(_extract_json(content))
    except json.JSONDecodeError as exc:
        print(f"\n❌ JSON не разобран: {exc}")
        return 2

    print("\n--- РАЗОБРАННЫЙ JSON ---")
    print(json.dumps(parsed, ensure_ascii=False, indent=2))

    zone = parsed.get("zone_type")
    total = parsed.get("total_width_mm")
    facades = parsed.get("facades") or []
    ok = zone == "Детская" and len(facades) == 0
    print("\n--- ВЕРДИКТ ---")
    print(f"zone_type      = {zone!r} (ожидание 'Детская')")
    print(f"total_width_mm = {total} (ожидание ≈2200)")
    print(f"facades        = {len(facades)} элементов (ожидание 0)")
    print(f"confidence     = {parsed.get('confidence')!r}")
    print(f"notes          = {parsed.get('notes')!r}")
    print("✅ OK: facades пуст, зона Детская" if ok
          else "❌ НЕ ОК: нужна итерация промпта")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
