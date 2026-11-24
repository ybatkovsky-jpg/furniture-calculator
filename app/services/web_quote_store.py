"""Постоянное хранение веб-расчётов (этап 1D).

`QUOTES` остаётся быстрым кэшем процесса, но источник истины — таблица
`web_quotes`. Если таблицы ещё нет (старая БД без миграции), запись/чтение
деградирует в память с предупреждением, а не роняет сервер.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.db.session import AsyncSessionLocal
from app.models.web_quote import WebQuote

logger = logging.getLogger(__name__)


async def save_web_quote(quote_id: str, data: dict[str, Any]) -> bool:
    """Сохранить снимок; True — записан в БД."""
    try:
        async with AsyncSessionLocal() as session:
            row = await session.get(WebQuote, quote_id)
            if row is None:
                row = WebQuote(id=quote_id)
                session.add(row)
            row.project_id = data.get("project_id")
            row.page_index = int(data.get("page_index") or 0)
            row.zone_type = data.get("zone")
            row.modules = data.get("modules")
            row.quote = data.get("quote")
            row.validation = data.get("validation")
            row.image_path = data.get("image")
            await session.commit()
        return True
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("Веб-расчёт %s не сохранён в БД: %s", quote_id, exc)
        return False


async def load_web_quote(quote_id: str) -> dict[str, Any] | None:
    """Прочитать снимок из БД; None — нет записи или БД недоступна."""
    try:
        async with AsyncSessionLocal() as session:
            row = await session.get(WebQuote, quote_id)
            if row is None:
                return None
            return {
                "quote": row.quote,
                "modules": row.modules,
                "validation": row.validation,
                "zone": row.zone_type,
                "image": row.image_path,
                "project_id": row.project_id,
                "page_index": row.page_index,
            }
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("Веб-расчёт %s не прочитан из БД: %s", quote_id, exc)
        return None
