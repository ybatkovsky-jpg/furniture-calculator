"""
Состояния FSM для ручного ввода модулей мебели.
"""

from aiogram.fsm.state import State, StatesGroup


class ManualInputStates(StatesGroup):
    """Состояния для ручного ввода модулей мебели."""

    waiting_for_module_type = State()     # Выбор типа модуля
    waiting_for_width = State()           # Ввод ширины
    waiting_for_custom_width = State()    # Ввод произвольной ширины
    waiting_for_depth = State()           # Ввод глубины (опционально)
    waiting_for_height = State()          # Ввод высоты (опционально)
    waiting_for_filling = State()         # Выбор наполнения
    waiting_for_quantity = State()        # Ввод количества дверей/ящиков
    waiting_for_glass = State()           # Вопрос о стекле в фасаде