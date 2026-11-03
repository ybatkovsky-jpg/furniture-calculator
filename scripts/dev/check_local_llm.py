"""Проверка локального LLM-сервера (Unsloth Studio / llama-server) на доступность.

Зачем: vision-контур проекта работает на локальной Qwen3.8-27B (GGUF), и когда
сервер на 192.168.1.133:8888 лежит, весь пайплайн молча деградирует (распознавание
не даёт модулей). Скрипт отвечает на вопрос «сервер жив?» за пару секунд и печатает,
что именно не так: нет TCP, не тот ключ, не та модель, не отвечает инференс.

Использование:
    python scripts/dev/check_local_llm.py                 # настройки из .env
    python scripts/dev/check_local_llm.py --url http://127.0.0.1:8888/v1   # на самой .133
    python scripts/dev/check_local_llm.py --no-generate    # только TCP + /v1/models

Коды выхода: 0 — сервер готов, 1 — не готов (с человеческим объяснением).
"""
import argparse
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402


def _tcp_ok(host: str, port: int, timeout: float = 3.0) -> tuple[bool, str]:
    """TCP-connect: отличаем 'никто не слушает' от 'пакет утонул в файрволе'."""
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ms = int((time.monotonic() - started) * 1000)
            return True, f"TCP {host}:{port} — открыт ({ms} мс)"
    except ConnectionRefusedError:
        return False, (
            f"TCP {host}:{port} — ОТКАЗ (ConnectionRefused): никто не слушает порт. "
            "Сервер не запущен либо слушает только 127.0.0.1 (нужен 0.0.0.0)."
        )
    except socket.timeout:
        return False, (
            f"TCP {host}:{port} — ТАЙМАУТ: хост не ответил. Обычно это файрвол "
            "на той машине (правило для 8888) или хост выключен."
        )
    except OSError as e:
        return False, f"TCP {host}:{port} — ошибка: {e}"


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"} if key else {}


def check(url: str, model: str, key: str, generate: bool) -> int:
    parsed = urlparse(url)
    host, port = parsed.hostname or "", parsed.port or (443 if parsed.scheme == "https" else 80)
    print(f"🔌 Сервер: {url}")
    print(f"   модель: {model or '(из /v1/models)'}")

    ok, message = _tcp_ok(host, port)
    print(("   ✅ " if ok else "   ❌ ") + message)
    if not ok:
        return 1

    base = url.rstrip("/")
    try:
        r = httpx.get(f"{base}/models", timeout=8, headers=_headers(key))
    except Exception as e:  # noqa: BLE001
        print(f"   ❌ GET /models — {type(e).__name__}: {e}")
        return 1

    if r.status_code in (401, 403):
        print(
            f"   ❌ GET /models — {r.status_code}: ключ не принят. "
            "Сверь LOCAL_LLM_API_KEY в .env с ключом Local Server на той машине."
        )
        return 1
    if r.status_code != 200:
        print(f"   ❌ GET /models — {r.status_code}: {r.text[:200]}")
        return 1

    try:
        ids = [m.get("id", "") for m in (r.json().get("data") or [])]
    except Exception:  # noqa: BLE001
        ids = []
    print(f"   ✅ GET /models — 200, моделей: {len(ids)}")
    if ids:
        print("      " + ", ".join(ids[:6]) + (" …" if len(ids) > 6 else ""))
    if model:
        if model in ids:
            print(f"   ✅ модель «{model}» загружена")
        else:
            print(
                f"   ⚠️ модели «{model}» в списке нет — сервер поднимет её при первом "
                "запросе (или упадёт). Проверь имя в Unsloth Studio."
            )

    if not generate:
        return 0

    # Реальная генерация: 3 токена, локально — бесплатно. Ловит «порт открыт,
    # но инференс не поднят» (OOM/модель не загрузилась).
    payload = {
        "model": model or (ids[0] if ids else ""),
        "messages": [{"role": "user", "content": "Скажи одно слово: готов"}],
        "max_tokens": 3,
        "temperature": 0,
    }
    try:
        r = httpx.post(f"{base}/chat/completions", timeout=120,
                       headers=_headers(key), json=payload)
    except Exception as e:  # noqa: BLE001
        print(f"   ❌ POST /chat/completions — {type(e).__name__}: {e}")
        return 1

    if r.status_code != 200:
        print(f"   ❌ POST /chat/completions — {r.status_code}: {r.text[:300]}")
        return 1
    try:
        answer = r.json()["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        answer = r.text[:200]
    print(f"   ✅ инференс работает — ответ: {answer!r}")
    print("\n🎯 Сервер готов: можно гонять распознавание.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=settings.local_llm_api_url,
                        help="базовый URL вида http://host:8888/v1 (по умолчанию из .env)")
    parser.add_argument("--model", default=settings.local_vision_model,
                        help="ожидаемое имя модели (по умолчанию LOCAL_VISION_MODEL)")
    parser.add_argument("--key", default=getattr(settings, "local_llm_api_key", ""),
                        help="API-ключ (по умолчанию LOCAL_LLM_API_KEY)")
    parser.add_argument("--no-generate", action="store_true",
                        help="не проверять генерацию (только TCP и список моделей)")
    args = parser.parse_args()

    if not args.url:
        print("❌ LOCAL_LLM_API_URL не задан ни в .env, ни в --url")
        return 1
    return check(args.url, args.model, args.key, not args.no_generate)


if __name__ == "__main__":
    sys.exit(main())
