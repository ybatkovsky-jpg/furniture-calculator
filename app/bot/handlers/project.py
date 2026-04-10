"""
Обработчики для создания и управления проектами.
"""

from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from app.bot.states.project import ProjectCreationStates
from app.db.session import get_session
from app.models.project import Project

router = Router()


@router.message(F.text == "📁 Новый проект")
async def start_project_creation(message: Message, state: FSMContext):
    """Начинает процесс создания нового проекта."""
    await state.set_state(ProjectCreationStates.waiting_for_client_name)
    await message.answer("Введите ФИО клиента:")


@router.message(ProjectCreationStates.waiting_for_client_name)
async def process_client_name(message: Message, state: FSMContext):
    """Обрабатывает ввод ФИО клиента."""
    await state.update_data(client_name=message.text)
    await state.set_state(ProjectCreationStates.waiting_for_phone)
    await message.answer("Телефон:")


@router.message(ProjectCreationStates.waiting_for_phone)
async def process_client_phone(message: Message, state: FSMContext):
    """Обрабатывает ввод телефона клиента."""
    await state.update_data(client_phone=message.text)
    await state.set_state(ProjectCreationStates.waiting_for_address)
    await message.answer("Адрес:")


@router.message(ProjectCreationStates.waiting_for_address)
async def process_client_address(message: Message, state: FSMContext):
    """Обрабатывает ввод адреса и создаёт проект."""
    data = await state.get_data()
    data['client_address'] = message.text
    
    # Создаём проект в БД
    async for session in get_session():
        project = Project(
            telegram_user_id=str(message.from_user.id),
            client_name=data['client_name'],
            client_phone=data['client_phone'],
            client_address=data['client_address'],
            status="draft"
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        
        # Сохраняем ID проекта в состоянии для дальнейшего использования
        await state.update_data(project_id=project.id)
        break
    
    await state.clear()
    
    # Клавиатура для выбора способа ввода данных
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📷 Фото чертежа", callback_data="input_photo")],
            [InlineKeyboardButton(text="✏️ Вручную", callback_data="input_manual")]
        ]
    )
    
    await message.answer(
        f"Проект создан!\n\n"
        f"Клиент: {data['client_name']}\n"
        f"Телефон: {data['client_phone']}\n"
        f"Адрес: {data['client_address']}\n\n"
        f"Как ввести данные?",
        reply_markup=keyboard
    )


@router.callback_query(F.data == "input_photo")
async def choose_photo_input(callback, state: FSMContext):
    """Обработка выбора ввода через фото."""
    await callback.message.answer("Отправьте фото чертежа для распознавания.")
    # Здесь можно установить состояние для ожидания фото
    # Пока просто ответим
    await callback.answer()


@router.callback_query(F.data == "input_manual")
async def choose_manual_input(callback, state: FSMContext):
    """Обработка выбора ручного ввода."""
    # Сохраняем project_id в состоянии для передачи в manual_input
    data = await state.get_data()
    await state.update_data(project_id=data.get('project_id'))
    
    # Перенаправляем на обработчик ручного ввода
    await callback.message.answer("Переходим к ручному вводу модулей.")
    # Устанавливаем состояние для начала ручного ввода
    from app.bot.states.manual_input import ManualInputStates
    await state.set_state(ManualInputStates.waiting_for_module_type)
    
    from app.bot.keyboards.inline import get_module_type_keyboard
    await callback.message.answer(
        "Выберите тип модуля:",
        reply_markup=get_module_type_keyboard()
    )
    await callback.answer()
