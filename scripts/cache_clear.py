"""Сброс кэша распознавания (P4).

Правила поменялись, а текст промпта — нет: без сброса вернётся старое.

Запуск:
    .\\.venv\\Scripts\\python.exe scripts/cache_clear.py --page 0002
    .\\.venv\\Scripts\\python.exe scripts/cache_clear.py --all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services import recognition_cache


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", help="страница альбома, например 0002")
    parser.add_argument("--all", action="store_true", help="удалить весь кэш")
    args = parser.parse_args()

    if args.all:
        removed = recognition_cache.clear()
        print(f"удалено файлов кэша: {removed}")
        return 0

    if not args.page:
        parser.error("нужен --page или --all")

    image = next(Path("input_images").glob(f"*page-{args.page}.jpg"), None)
    if image is None:
        print(f"страница {args.page} не найдена")
        return 1

    removed = recognition_cache.clear(image)
    print(f"{image.name}: удалено файлов кэша {removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
