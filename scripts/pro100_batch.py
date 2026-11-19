"""Пакетная выгрузка деталировки из всех .sto через PRO100.

Для каждого проекта запускает PRO100, ждёт загрузки, выгружает отчёты в
`docs/pro100_reports/<имя>/` и пишет сводку размеров в JSON. Проекты, где
деталировка пустая (собраны из элементов библиотек), помечаются `empty`.

Запуск: .\\.venv\\Scripts\\python.exe scripts/pro100_batch.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("Olga_project")
OUT_ROOT = Path("docs/pro100_reports")
SUMMARY = Path("docs/pro100_reports_summary.json")
PY = sys.executable


def main() -> int:
    projects = sorted(ROOT.rglob("*.sto"))
    limit = int(os.getenv("PRO100_LIMIT", "0"))
    if limit:
        projects = projects[:limit]
    print(f"проектов: {len(projects)}")
    summary = []
    for index, sto in enumerate(projects, start=1):
        target = OUT_ROOT / sto.stem[:60]
        if (target / "details.txt").exists() and (target / "details.txt").stat().st_size > 500:
            summary.append({"file": str(sto), "status": "cached"})
            continue
        # PRO100 не подхватывает проект, если предыдущий экземпляр ещё жив.
        subprocess.run(["taskkill", "/IM", "PRO100.exe", "/F"],
                       capture_output=True, check=False)
        time.sleep(3)
        run = subprocess.run(
            [PY, "scripts/pro100_export.py", str(sto), str(target)],
            capture_output=True,
            check=False,
        )
        details = target / "details.txt"
        size = details.stat().st_size if details.exists() else 0
        status = "ok" if size > 500 else "empty"
        if status == "empty":
            tail = run.stdout.decode("utf-8", "ignore").strip().splitlines()
            print("   debug:", tail[-1] if tail else "(нет вывода)")
        summary.append({"file": str(sto), "status": status, "details_bytes": size})
        SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{index:2}/{len(projects)} {sto.name[:45]:45} {status} {size}")

    ok = sum(1 for row in summary if row["status"] in ("ok", "cached"))
    print(f"\nс деталировкой: {ok} из {len(projects)} -> {SUMMARY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
