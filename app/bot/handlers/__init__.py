"""
Handlers для Telegram бота.
"""

from app.bot.handlers.start import router as start_router
from app.bot.handlers.price_admin import router as price_router
from app.bot.handlers.project import router as project_router
from app.bot.handlers.image import router as image_router
from app.bot.handlers.manual_input import router as manual_input_router
from app.bot.handlers.materials import router as materials_router
from app.bot.handlers.calculation import router as calculation_router
from app.bot.handlers.discounts import router as discounts_router
from app.bot.handlers.kp import router as kp_router

__all__ = ["start_router", "price_router", "project_router", "image_router", "manual_input_router", "materials_router", "calculation_router", "discounts_router", "kp_router"]
