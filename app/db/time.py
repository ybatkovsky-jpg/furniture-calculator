"""Время в UTC без устаревшего datetime.utcnow.

Значения остаются наивными (без tzinfo) — схема БД не меняется, миграция не нужна.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Текущее время UTC без tzinfo (совместимо с колонками DateTime)."""
    return datetime.now(UTC).replace(tzinfo=None)
