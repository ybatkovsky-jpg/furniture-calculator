"""Middleware доступа к боту (allowlist пользователей)."""

from app.bot.middlewares.access import AllowlistMiddleware

__all__ = ["AllowlistMiddleware"]
