from aiogram.fsm.state import State, StatesGroup


class PriceAdminStates(StatesGroup):
    waiting_for_file = State()
