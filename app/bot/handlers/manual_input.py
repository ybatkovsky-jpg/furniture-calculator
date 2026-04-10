"""
Обработчики для ручного ввода модулей мебели.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from app.bot.states.manual_input import ManualInputStates
from app.bot.keyboards.inline import (
    get_module_type_keyboard,
    get_width_keyboard,
    get_filling_keyboard,
    get_glass_keyboard,
    get_module_actions_keyboard
)
from app.db.session import get_session
from app.models.calculation import Calculation

router = Router()


@router.callback_query(F.data == "input_manual")
async def start_manual_input(callback: CallbackQuery, state: FSMContext):
    """Начинает процесс ручного ввода модулей."""
    # Получаем данные проекта из состояния
    data = await state.get_data()
    project_id = data.get('project_id')

    print(f"DEBUG: start_manual_input - data={data}, project_id={project_id}")

    if not project_id:
        await callback.message.answer("Ошибка: проект не найден. Начните с создания нового проекта.")
        await callback.answer()
        return

    # Создаём новый расчёт для проекта
    async for session in get_session():
        calculation = Calculation(project_id=project_id, modules=[])
        session.add(calculation)
        await session.commit()
        await session.refresh(calculation)

        # Сохраняем ID расчёта в состоянии
        await state.update_data(calculation_id=calculation.id, current_modules=[])
        break

    await state.set_state(ManualInputStates.waiting_for_module_type)

    await callback.message.answer(
        "Выберите тип модуля:",
        reply_markup=get_module_type_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("module_type:"), ManualInputStates.waiting_for_module_type)
async def process_module_type(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор типа модуля."""
    module_type = callback.data.split(":")[1]

    # Сохраняем тип модуля
    data = await state.get_data()
    current_modules = data.get('current_modules', [])
    current_module = {'type': module_type}
    current_modules.append(current_module)
    await state.update_data(current_modules=current_modules, current_module_index=len(current_modules) - 1)

    await state.set_state(ManualInputStates.waiting_for_width)

    await callback.message.answer(
        "Выберите ширину модуля (мм):",
        reply_markup=get_width_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("width:"), ManualInputStates.waiting_for_width)
async def process_width(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор ширины модуля."""
    width_value = callback.data.split(":")[1]

    if width_value == "custom":
        await state.set_state(ManualInputStates.waiting_for_custom_width)
        await callback.message.answer("Введите ширину в мм (например: 650):")
    else:
        await save_width_and_continue(callback, state, int(width_value))

    await callback.answer()


@router.message(ManualInputStates.waiting_for_custom_width)
async def process_custom_width(message: Message, state: FSMContext):
    """Обрабатывает ввод произвольной ширины."""
    try:
        width = int(message.text.strip())
        if width <= 0:
            raise ValueError("Ширина должна быть положительной")

        await save_width_and_continue(message, state, width)
    except ValueError:
        await message.answer("Введите корректную ширину в мм (положительное число):")


async def save_width_and_continue(source, state: FSMContext, width: int):
    """Сохраняет ширину и переходит к следующему шагу."""
    data = await state.get_data()
    current_modules = data.get('current_modules', [])
    current_module_index = data.get('current_module_index', 0)

    if current_module_index < len(current_modules):
        current_modules[current_module_index]['width'] = width
        await state.update_data(current_modules=current_modules)

    # Определяем глубину по умолчанию
    module_type = current_modules[current_module_index]['type']
    default_depth = 320 if module_type in ['upper_base', 'penal'] else 560
    current_modules[current_module_index]['depth'] = default_depth

    # Определяем высоту по умолчанию
    if module_type == 'penal':
        default_height = 2100
    elif module_type == 'upper_base':
        default_height = 720
    else:  # lower_base, column, cabinet
        default_height = 820

    current_modules[current_module_index]['height'] = default_height
    await state.update_data(current_modules=current_modules)

    await state.set_state(ManualInputStates.waiting_for_filling)

    if hasattr(source, 'message'):
        # Это callback query
        await source.message.answer(
            f"Ширина: {width} мм\n"
            f"Глубина: {default_depth} мм (по умолчанию)\n"
            f"Высота: {default_height} мм (по умолчанию)\n\n"
            "Выберите наполнение:",
            reply_markup=get_filling_keyboard()
        )
    else:
        # Это обычное сообщение
        await source.answer(
            f"Ширина: {width} мм\n"
            f"Глубина: {default_depth} мм (по умолчанию)\n"
            f"Высота: {default_height} мм (по умолчанию)\n\n"
            "Выберите наполнение:",
            reply_markup=get_filling_keyboard()
        )


@router.callback_query(F.data.startswith("filling:"), ManualInputStates.waiting_for_filling)
async def process_filling(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор наполнения модуля."""
    filling_type = callback.data.split(":")[1]

    data = await state.get_data()
    current_modules = data.get('current_modules', [])
    current_module_index = data.get('current_module_index', 0)

    if current_module_index < len(current_modules):
        current_modules[current_module_index]['filling'] = filling_type
        await state.update_data(current_modules=current_modules)

    # Для некоторых типов наполнения спрашиваем количество
    if filling_type in ['doors', 'drawers', 'shelves']:
        await state.set_state(ManualInputStates.waiting_for_quantity)
        filling_names = {
            'doors': 'дверей',
            'drawers': 'ящиков',
            'shelves': 'полок'
        }
        await callback.message.answer(f"Введите количество {filling_names[filling_type]}:")
    else:
        # Для подъёмника и бутылочницы количество = 1
        current_modules[current_module_index]['quantity'] = 1
        await state.update_data(current_modules=current_modules)
        await ask_about_glass(callback, state)

    await callback.answer()


@router.message(ManualInputStates.waiting_for_quantity)
async def process_quantity(message: Message, state: FSMContext):
    """Обрабатывает ввод количества дверей/ящиков/полок."""
    try:
        quantity = int(message.text.strip())
        if quantity <= 0:
            raise ValueError("Количество должно быть положительным")

        data = await state.get_data()
        current_modules = data.get('current_modules', [])
        current_module_index = data.get('current_module_index', 0)

        if current_module_index < len(current_modules):
            current_modules[current_module_index]['quantity'] = quantity
            await state.update_data(current_modules=current_modules)

        await ask_about_glass(message, state)
    except ValueError:
        await message.answer("Введите корректное количество (положительное число):")


async def ask_about_glass(source, state: FSMContext):
    """Спрашивает о стекле в фасаде."""
    await state.set_state(ManualInputStates.waiting_for_glass)

    if hasattr(source, 'message'):
        # Это callback query
        await source.message.answer(
            "Есть ли стекло в фасаде?",
            reply_markup=get_glass_keyboard()
        )
    else:
        # Это обычное сообщение
        await source.answer(
            "Есть ли стекло в фасаде?",
            reply_markup=get_glass_keyboard()
        )


@router.callback_query(F.data.startswith("glass:"), ManualInputStates.waiting_for_glass)
async def process_glass(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает ответ о стекле в фасаде."""
    glass_answer = callback.data.split(":")[1]
    has_glass = glass_answer == "yes"

    data = await state.get_data()
    current_modules = data.get('current_modules', [])
    current_module_index = data.get('current_module_index', 0)

    if current_module_index < len(current_modules):
        current_modules[current_module_index]['has_glass'] = has_glass
        await state.update_data(current_modules=current_modules)

    # Показываем резюме модуля
    module = current_modules[current_module_index]
    module_type_names = {
        'lower_base': 'Нижняя база',
        'upper_base': 'Верхняя база',
        'penal': 'Пенал',
        'column': 'Колонна',
        'cabinet': 'Тумба'
    }

    filling_names = {
        'doors': 'Двери',
        'drawers': 'Ящики',
        'shelves': 'Полки',
        'lift': 'Подъёмник',
        'wine_rack': 'Бутылочница'
    }

    summary = (
        f"📦 Модуль добавлен:\n\n"
        f"Тип: {module_type_names.get(module['type'], module['type'])}\n"
        f"Размеры: {module['width']}×{module['depth']}×{module['height']} мм\n"
        f"Наполнение: {filling_names.get(module['filling'], module['filling'])}\n"
        f"Количество: {module.get('quantity', 1)}\n"
        f"Стекло в фасаде: {'Да' if module.get('has_glass', False) else 'Нет'}"
    )

    await callback.message.answer(summary, reply_markup=get_module_actions_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("action:"))
async def process_module_action(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает действия после ввода модуля."""
    action = callback.data.split(":")[1]

    if action == "add_module":
        # Начинаем ввод нового модуля
        await state.set_state(ManualInputStates.waiting_for_module_type)
        await callback.message.answer(
            "Выберите тип следующего модуля:",
            reply_markup=get_module_type_keyboard()
        )
    elif action == "select_materials":
        # Сохраняем все модули в базу данных
        data = await state.get_data()
        current_modules = data.get('current_modules', [])
        calculation_id = data.get('calculation_id')

        if calculation_id and current_modules:
            async for session in get_session():
                calculation = await session.get(Calculation, calculation_id)
                if calculation:
                    calculation.modules = current_modules
                    await session.commit()
                break

        await callback.message.answer(
            f"✅ Ввод модулей завершён!\n"
            f"Добавлено модулей: {len(current_modules)}\n\n"
            "Теперь выберите материалы для изготовления."
        )

        if not calculation_id:
            await callback.message.answer("❌ Ошибка: ID расчёта не найден")
            await callback.answer()
            return

        # Начинаем выбор материалов (состояние не очищаем, так как процесс продолжается)
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"STARTING MATERIAL SELECTION: calculation_id={calculation_id}")
        from app.bot.handlers.materials import start_material_selection
        await start_material_selection(callback.message, calculation_id, state)

    await callback.answer()
