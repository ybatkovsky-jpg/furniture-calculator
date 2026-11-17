"""Экспорт Excel из снимка расчёта (P1-14).

Один путь для бота и CLI: `fill_template_from_quote` (P0-02). Файл пишется
во временный каталог ОС (не в `temp/` репозитория) и удаляется вызывающим
кодом после отправки — исходники и артефакты прогонов не засоряются.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from app.services.template_filler import fill_template_from_quote

DEFAULT_TEMPLATE = (
    Path(__file__).resolve().parents[2] / "templates" / "Таблица для расчетов пустая.xlsx"
)


def render_quote_excel(
    quote: dict,
    output_path: str | Path,
    *,
    sheet_name: str | None = None,
    template_path: str | Path | None = None,
) -> str:
    """Заполнить шаблон из снимка (синхронно; openpyxl не async)."""
    return fill_template_from_quote(
        quote,
        str(template_path or DEFAULT_TEMPLATE),
        str(output_path),
        sheet_name=sheet_name,
    )


async def render_quote_excel_async(
    quote: dict,
    *,
    sheet_name: str | None = None,
    template_path: str | Path | None = None,
) -> Path:
    """Собрать xlsx во временном файле, не блокируя event loop.

    Возвращает путь; вызывающий обязан удалить файл в `finally`.
    """
    if not quote or not quote.get("lines"):
        raise ValueError("Снимок расчёта пуст: Excel не из чего собирать")
    fd, name = tempfile.mkstemp(prefix="smeta_", suffix=".xlsx")
    os.close(fd)
    path = Path(name)
    try:
        await asyncio.to_thread(
            render_quote_excel,
            quote,
            path,
            sheet_name=sheet_name,
            template_path=template_path,
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def safe_excel_filename(calculation_id: int, project_name: str | None = None) -> str:
    """Имя файла без ПДн и спецсимволов: «Смета_Кухня_12.xlsx»."""
    base = " ".join(str(project_name or "Смета").split())
    cleaned = "".join(
        ch for ch in base if ch.isalnum() or ch in " _-"
    ).strip() or "Смета"
    return f"Смета_{cleaned[:40]}_{int(calculation_id)}.xlsx"
