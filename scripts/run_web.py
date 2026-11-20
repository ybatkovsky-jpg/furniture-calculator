"""Запуск веб-интерфейса: `.\\venv\\Scripts\\python.exe scripts/run_web.py`.

Слушает только 127.0.0.1 — для локальной обкатки. Если нужен доступ из
локальной сети офиса, поменяйте host на 0.0.0.0 и добавьте авторизацию.
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HOST = "127.0.0.1"
PORT = 8000


def main() -> int:
    url = f"http://{HOST}:{PORT}/"
    print(f"Веб-интерфейс: {url}")
    print("Остановка — Ctrl+C")
    webbrowser.open(url)
    uvicorn.run("app.web.server:app", host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
