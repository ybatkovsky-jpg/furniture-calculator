"""
Обработчик команды /start.
"""

from aiogram import Router
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command

router = Router()


START_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📁 Новый проект"), KeyboardButton(text="📋 Мои проекты")],
        [KeyboardButton(text="💰 Прайс"), KeyboardButton(text="⚙️ Настройки")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start."""
    await message.answer(
        "👋 Привет! Я помогу рассчитать стоимость мебели.\n\n"
        "Выберите действие:",
        reply_markup=START_KEYBOARD,
    )
