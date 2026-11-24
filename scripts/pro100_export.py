"""Выгрузка отчётов PRO100 по .sto-проекту в текст.

PRO100 не имеет командной строки для отчётов, но у него есть нативные
Win32-меню и стандартные диалоги. Скрипт:
1. запускает PRO100 с проектом;
2. посылает команду меню «Отчёты» (id 148);
3. нажимает «Сохранить всё...» и заполняет диалоги сохранения;
4. складывает details/elements/materials/estimate в папку проекта;
5. закрывает PRO100.

Запуск: .\\.venv\\Scripts\\python.exe scripts/pro100_export.py "путь\\к\\файлу.sto"
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path

EXE = Path("D:/ПРОГРАММЫ/PRO100/PRO100v7.08x64/PRO100.exe")
WM_COMMAND, WM_SETTEXT, BM_CLICK, WM_CLOSE = 0x0111, 0x000C, 0x00F5, 0x0010
CMD_REPORTS = 148
LOAD_DELAY = float(os.getenv("PRO100_LOAD_DELAY", "20"))

user32 = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

TAGS = {
    "деталей": "details",
    "элементов": "elements",
    "материалах": "materials",
    "стоимости": "estimate",
}


def _visible(hwnd) -> bool:
    return bool(user32.IsWindowVisible(hwnd))


def _title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _pid(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _windows(process_id: int) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []

    def callback(hwnd, _):
        if _visible(hwnd) and _pid(hwnd) == process_id:
            found.append((hwnd, _class(hwnd), _title(hwnd)))
        return True

    user32.EnumWindows(EnumProc(callback), 0)
    return found


def _children(hwnd) -> list[tuple[int, str, str, int]]:
    items: list[tuple[int, str, str, int]] = []

    def callback(child, _):
        items.append((child, _class(child), _title(child), user32.GetDlgCtrlID(child)))
        return True

    user32.EnumChildWindows(hwnd, EnumProc(callback), 0)
    return items


def _wait(predicate, timeout: float = 60.0, step: float = 0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(step)
    return None


def capture_window(hwnd: int, target: Path) -> bool:
    """Снимок окна PRO100: визуальный контекст проекта для менеджера."""
    try:
        from PIL import ImageGrab
    except ImportError:
        return False
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.8)
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        image = ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.bottom)
        )
        image.save(target, format="PNG")
        return True
    except Exception:  # noqa: BLE001 — снимок не должен ломать выгрузку
        return False


def export(sto: Path, out_dir: Path) -> dict:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # Сохраняем во временную ASCII-папку: диалог сохранения PRO100 не принимает
    # путь с кириллицей и молча не пишет файл.
    save_dir = (Path("temp/pro100_save") / uuid.uuid4().hex[:8]).resolve()
    save_dir.mkdir(parents=True, exist_ok=True)
    # PRO100 не открывает проект по пути с кириллицей (молча пустой проект),
    # поэтому копируем во временный ASCII-путь и открываем копию.
    work = Path("temp/pro100_work")
    work.mkdir(parents=True, exist_ok=True)
    local = work / f"p_{uuid.uuid4().hex[:8]}.sto"
    shutil.copy2(sto, local)

    process = subprocess.Popen([str(EXE), str(local)])
    pid = process.pid

    main = _wait(lambda: next((w for w in _windows(pid) if w[1] == "TMainForm"), None), 90)
    if not main:
        process.kill()
        return {"status": "no_main_window"}
    main_hwnd = main[0]
    start = time.time()
    # Окно появляется раньше, чем проект дочитан с диска. При загрузке
    # PRO100 показывает модальное «Материал не найден» — без него проект
    # не раскрывается и отчёты пустые. Пропускаем все такие окна.
    for _ in range(40):
        time.sleep(3)
        dialog = next(
            (w for w in _windows(pid) if w[1] == "TTextureSubstituteForm"),
            None,
        )
        if dialog:
            skip = next(
                (c[0] for c in _children(dialog[0]) if c[2].strip() == "Пропустить всё"),
                None,
            )
            if skip:
                user32.PostMessageW(skip, BM_CLICK, 0, 0)
                time.sleep(1)
            continue
        if time.time() - start >= LOAD_DELAY:
            break
    time.sleep(2)

    screenshot = out_dir / "screenshot.png"
    screenshot_ok = capture_window(main_hwnd, screenshot)

    user32.PostMessageW(main_hwnd, WM_COMMAND, CMD_REPORTS, 0)
    report = _wait(lambda: next((w for w in _windows(pid) if w[2] == "Отчёты"), None), 60)
    if not report:
        user32.PostMessageW(main_hwnd, WM_CLOSE, 0, 0)
        return {"status": "no_reports_dialog"}
    report_hwnd = report[0]
    time.sleep(2)

    save_all = next(
        (c[0] for c in _children(report_hwnd) if c[2].strip() == "Сохранить всё..."),
        None,
    )
    if not save_all:
        user32.PostMessageW(main_hwnd, WM_CLOSE, 0, 0)
        return {"status": "no_save_all"}
    user32.PostMessageW(save_all, BM_CLICK, 0, 0)

    saved = []
    for _ in range(8):
        dialog = _wait(
            lambda: next(
                (w for w in _windows(pid) if w[1] == "#32770" and "Сохранение" in w[2]),
                None,
            ),
            20,
        )
        if not dialog:
            break
        hwnd, _, title = dialog
        tag = next((v for k, v in TAGS.items() if k in title), "report")
        edit = next((c[0] for c in _children(hwnd) if c[1] == "Edit" and c[3] == 1001), None)
        button = next((c[0] for c in _children(hwnd) if c[1] == "Button" and c[3] == 1), None)
        if not edit or not button:
            break
        user32.SendMessageW(edit, WM_SETTEXT, 0, str(save_dir / tag))
        time.sleep(0.4)
        user32.PostMessageW(button, BM_CLICK, 0, 0)
        time.sleep(2.0)
        confirm = _wait(
            lambda: next(
                (w for w in _windows(pid) if w[1] == "#32770"
                 and ("Заменить" in w[2] or "существует" in w[2])),
                None,
            ),
            5,
        )
        if confirm:
            yes = next(
                (c[0] for c in _children(confirm[0]) if c[1] == "Button" and c[3] in (6, 1)),
                None,
            )
            if yes:
                user32.PostMessageW(yes, BM_CLICK, 0, 0)
                time.sleep(1.0)
        saved.append(tag)

    user32.PostMessageW(report_hwnd, WM_CLOSE, 0, 0)
    time.sleep(0.5)
    user32.PostMessageW(main_hwnd, WM_CLOSE, 0, 0)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()

    local.unlink(missing_ok=True)
    for produced in save_dir.glob("*"):
        if produced.is_file():
            shutil.move(str(produced), str(out_dir / produced.name))
    shutil.rmtree(save_dir, ignore_errors=True)
    files = sorted(p.name for p in out_dir.iterdir() if p.is_file())
    sizes = {p.name: p.stat().st_size for p in out_dir.iterdir() if p.is_file()}
    return {
        "status": "ok",
        "saved": saved,
        "files": files,
        "sizes": sizes,
        "pid": pid,
        "screenshot": "screenshot.png" if screenshot_ok else None,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: pro100_export.py <file.sto> [out_dir]")
        return 2
    sto = Path(sys.argv[1]).resolve()
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else (
        Path("docs/pro100_reports") / sto.stem
    )
    result = export(sto, out_dir)
    print(result)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
