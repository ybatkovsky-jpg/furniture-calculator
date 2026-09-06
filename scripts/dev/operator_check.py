#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Operator vision cross-check for furniture drawings (альбом «Рокоссовского 59-79»).

Reads an album page image, base64-encodes it as a data URL and sends it together
with a question to the local vision API (orcarouter/Qwen3.8-27B-Uncensored-GGUF
via http://192.168.1.133:8888/v1/chat/completions), then prints the model answer
in UTF-8.

Usage:
  python scripts/dev/operator_check.py 0008 --question "Текст вопроса"

The page number is zero-padded, e.g. "0001", "0008".
"""

import argparse
import base64
import json
import sys

import httpx

API_URL = "http://192.168.1.133:8888/v1/chat/completions"
API_KEY = "sk-unsloth-c10db5079f5c4a4a99025ea350ef28c6"
MODEL = "orcarouter/Qwen3.8-27B-Uncensored-GGUF"
MAX_TOKENS = 500
TIMEOUT = 300.0
MAX_ATTEMPTS = 3  # 1 original attempt + up to 2 retries
IMG_TEMPLATE = "input_images/Альбом чертежей Рокоссовского-59-79_page-{page}.jpg"


def build_payload(page: str, question: str) -> dict:
    img_path = IMG_TEMPLATE.format(page=page)
    with open(img_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": question},
                ],
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page", help='Номер страницы альбома, например "0008"')
    parser.add_argument(
        "--question", required=True, help="Текст вопроса к модели (на русском)"
    )
    parser.add_argument(
        "--json", action="store_true", help="Печатать только answer как JSON-строку"
    )
    args = parser.parse_args()

    payload = build_payload(args.page, args.question)
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    answer = None
    last_error = None
    with httpx.Client(timeout=TIMEOUT) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                if attempt > 1:
                    sys.stderr.write(
                        f"[operator_check] retry {attempt - 1} of "
                        f"{MAX_ATTEMPTS - 1} for page {args.page}\n"
                    )
                resp = client.post(API_URL, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                answer = data["choices"][0]["message"]["content"]
                break
            except Exception as exc:  # noqa: BLE001 - any failure triggers retry
                last_error = f"{type(exc).__name__}: {exc}"
                sys.stderr.write(
                    f"[operator_check] page {args.page} attempt {attempt} "
                    f"failed: {last_error}\n"
                )
                if attempt < MAX_ATTEMPTS:
                    continue
                sys.stderr.write(
                    f"[operator_check] page {args.page} FAILED after "
                    f"{MAX_ATTEMPTS} attempts: {last_error}\n"
                )
                return 1

    if answer is None:
        sys.stderr.write("[operator_check] empty answer from API\n")
        return 1

    out = json.dumps(answer, ensure_ascii=False) if args.json else answer
    sys.stdout.buffer.write(out.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
