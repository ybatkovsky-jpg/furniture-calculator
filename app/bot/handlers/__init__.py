"""
Handlers для Telegram бота.
"""

from app.bot.handlers.start import router as start_router
from app.bot.handlers.price_admin import router as price_router
from app.bot.handlers.project import router as project_router

__all__ = ["start_router", "price_router", "project_router"]
