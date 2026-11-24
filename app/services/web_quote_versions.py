"""История редакций веб-расчёта (этап 2)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import AsyncSessionLocal
from app.models.web_quote_version import WebQuoteVersion

logger = logging.getLogger(__name__)


async def save_web_quote_version(
    quote_id: str,
    data: dict[str, Any],
    *,
    reason: str = "web-edit",
    created_by: str = "web",
) -> int | None:
    """Добавить версию снимка; вернуть номер или None при ошибке БД."""
    try:
        async with AsyncSessionLocal() as session:
            previous = await session.scalar(
                select(func.max(WebQuoteVersion.version_no)).where(
                    WebQuoteVersion.quote_id == quote_id
                )
            )
            version_no = int(previous or 0) + 1
            session.add(
                WebQuoteVersion(
                    quote_id=quote_id,
                    version_no=version_no,
                    modules=data.get("modules"),
                    quote=data.get("quote"),
                    validation=data.get("validation"),
                    reason=reason,
                    created_by=created_by,
                )
            )
            await session.commit()
        return version_no
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("Версия расчёта %s не сохранена: %s", quote_id, exc)
        return None


async def list_web_quote_versions(quote_id: str) -> list[dict[str, Any]]:
    """История редакций по возрастанию номера."""
    try:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(WebQuoteVersion)
                    .where(WebQuoteVersion.quote_id == quote_id)
                    .order_by(WebQuoteVersion.version_no)
                )
            ).all()
            return [
                {
                    "version_no": row.version_no,
                    "reason": row.reason,
                    "created_by": row.created_by,
                    "created_at": row.created_at.isoformat()
                    if row.created_at
                    else None,
                    "modules_count": len(row.modules or []),
                    "can_calculate": bool(
                        (row.validation or {}).get("can_calculate", True)
                    ),
                }
                for row in rows
            ]
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("История расчёта %s не прочитана: %s", quote_id, exc)
        return []
