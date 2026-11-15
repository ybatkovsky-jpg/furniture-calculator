"""Allowlist пользователей бота (часть P0-05: контроль доступа).

Работает только при заполненном ALLOWED_TELEGRAM_IDS: пустой список означает
«ограничения нет», иначе после обновления бот стал бы недоступен всем, включая
собственника. Админы (ADMIN_TELEGRAM_IDS) допускаются всегда.
"""

import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import settings

logger = logging.getLogger(__name__)


class AllowlistMiddleware(BaseMiddleware):
    """Не пропускает в обработчики пользователей вне allowlist."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = getattr(event, "from_user", None)
        if user is None or settings.is_allowed(user.id):
            return await handler(event, data)

        logger.warning(
            "Отказ в доступе к боту: user_id=%s username=%s",
            user.id,
            user.username,
        )
        if isinstance(event, Message):
            await event.answer(
                "⛔ Доступ к боту ограничен. Обратитесь к администратору."
            )
        elif isinstance(event, CallbackQuery):
            await event.answer("⛔ Доступ ограничен", show_alert=True)
        return None
