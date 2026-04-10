"""
Состояния FSM для выбора материалов.
"""

from aiogram.fsm.state import State, StatesGroup


class MaterialSelectionStates(StatesGroup):
    """Состояния для выбора материалов."""

    # ЛДСП
    selecting_ldsp = State()  # Выбор ЛДСП из списка

    # Фасады
    selecting_facade_category = State()  # Выбор категории фасадов
    selecting_facade_item = State()      # Выбор конкретного фасада

    # Кромка - автоматический выбор
    # Петли
    selecting_hinge_brand = State()      # Выбор бренда петель
    selecting_hinge_model = State()      # Выбор модели петель

    # Ящики (если есть)
    selecting_drawer_brand = State()     # Выбор бренда ящиков
    selecting_drawer_model = State()     # Выбор модели ящиков

    # Ручной ввод цены для позиций с requires_manual_price=1
    waiting_for_manual_price = State()   # Ожидание ручного ввода цены