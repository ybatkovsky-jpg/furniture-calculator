"""
Inline клавиатуры для бота.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_module_type_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора типа модуля."""
    keyboard = [
        [
            InlineKeyboardButton(text="Нижняя база", callback_data="module_type:lower_base"),
            InlineKeyboardButton(text="Верхняя база", callback_data="module_type:upper_base")
        ],
        [
            InlineKeyboardButton(text="Пенал", callback_data="module_type:penal"),
            InlineKeyboardButton(text="Колонна", callback_data="module_type:column")
        ],
        [
            InlineKeyboardButton(text="Тумба", callback_data="module_type:cabinet")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_width_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора ширины модуля."""
    keyboard = [
        [
            InlineKeyboardButton(text="300", callback_data="width:300"),
            InlineKeyboardButton(text="400", callback_data="width:400"),
            InlineKeyboardButton(text="450", callback_data="width:450")
        ],
        [
            InlineKeyboardButton(text="500", callback_data="width:500"),
            InlineKeyboardButton(text="600", callback_data="width:600"),
            InlineKeyboardButton(text="800", callback_data="width:800")
        ],
        [
            InlineKeyboardButton(text="900", callback_data="width:900"),
            InlineKeyboardButton(text="Другая", callback_data="width:custom")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_filling_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора наполнения модуля."""
    keyboard = [
        [
            InlineKeyboardButton(text="Двери", callback_data="filling:doors"),
            InlineKeyboardButton(text="Ящики", callback_data="filling:drawers")
        ],
        [
            InlineKeyboardButton(text="Полки", callback_data="filling:shelves"),
            InlineKeyboardButton(text="Подъёмник", callback_data="filling:lift")
        ],
        [
            InlineKeyboardButton(text="Бутылочница", callback_data="filling:wine_rack")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_glass_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для вопроса о стекле в фасаде."""
    keyboard = [
        [
            InlineKeyboardButton(text="Да", callback_data="glass:yes"),
            InlineKeyboardButton(text="Нет", callback_data="glass:no")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_module_actions_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура действий после ввода модуля."""
    keyboard = [
        [
            InlineKeyboardButton(text="➕ Ещё модуль", callback_data="action:add_module"),
            InlineKeyboardButton(text="✅ Готово", callback_data="action:done")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)