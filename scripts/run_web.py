"""Запуск веб-интерфейса: `.\\venv\\Scripts\\python.exe scripts/run_web.py`.

Слушает только 127.0.0.1 — для локальной обкатки. Если нужен доступ из
локальной сети офиса, поменяйте host на 0.0.0.0 и добавьте авторизацию.
"""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HOST = "127.0.0.1"
PORT = 8000
LOCK_PATH = Path(__file__).resolve().parent.parent / "data" / "web.lock"


def _acquire_lock():
    """Не дать запустить второй экземпляр веб-сервера."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("w")
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main() -> int:
    lock = _acquire_lock()
    if lock is None:
        print("Веб-сервер уже запущен (data/web.lock). Второй экземпляр не нужен.")
        return 1
    url = f"http://{HOST}:{PORT}/"
    print(f"Веб-интерфейс: {url}")
    print("Остановка — Ctrl+C")
    webbrowser.open(url)
    try:
        uvicorn.run("app.web.server:app", host=HOST, port=PORT, log_level="warning")
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
