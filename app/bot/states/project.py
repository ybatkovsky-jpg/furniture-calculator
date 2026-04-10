"""
Состояния FSM для создания и управления проектами.
"""

from aiogram.fsm.state import State, StatesGroup


class ProjectCreationStates(StatesGroup):
    """Состояния для создания нового проекта."""
    
    waiting_for_client_name = State()  # Ожидание ФИО клиента
    waiting_for_phone = State()        # Ожидание телефона
    waiting_for_address = State()      # Ожидание адреса