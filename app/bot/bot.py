"""
Инициализация aiogram Bot и Dispatcher.
"""

import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from app.config import settings

logger = logging.getLogger(__name__)

# Инициализируем бота с токеном из .env
bot = Bot(token=settings.telegram_bot_token)

# Хранилище состояний FSM (в памяти)
# Для production рекомендуется использовать Redis
storage = MemoryStorage()

# Диспетчер для управления handlers
dp = Dispatcher(storage=storage)


async def setup_bot_commands():
    """Установить список команд в меню Telegram-бота."""
    commands = [
        ("start", "⏬ Главное меню"),
        ("help", "❓ Помощь"),
        ("price_list", "💰 Список цен"),
    ]
    
    from aiogram.types import BotCommand
    
    bot_commands = [BotCommand(command=cmd, description=desc) for cmd, desc in commands]
    await bot.set_my_commands(bot_commands)
    logger.info("✓ Команды бота установлены")
