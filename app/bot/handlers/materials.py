"""
Обработчики для выбора материалов из прайса.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.states.materials import MaterialSelectionStates
from app.db.session import get_session
from app.models.price import PriceItem
from app.services.access import get_owned_calculation

router = Router()


async def start_material_selection(message: Message, calculation_id: int, state: FSMContext):
    """Начинает процесс выбора материалов."""
    # Проверяем, что пользователь существует
    if not message.from_user:
        await message.answer("Ошибка: пользователь не найден.")
        return

    # Сохраняем calculation_id в состоянии FSM
    await state.update_data(calculation_id=calculation_id)

    # Начинаем с выбора ЛДСП
    await select_ldsp(message)


async def select_ldsp(message: Message):
    """Показывает выбор ЛДСП из прайса."""
    async for session in get_session():
        # Получаем все позиции ЛДСП из базы
        result = await session.execute(
            select(PriceItem).where(
                PriceItem.category == "ЛДСП",
                PriceItem.is_active == 1
            ).order_by(PriceItem.name)
        )
        ldsp_items = result.scalars().all()
        break

    if not ldsp_items:
        await message.answer("❌ В прайсе не найдено ЛДСП. Обратитесь к администратору.")
        return

    # Создаём клавиатуру с ЛДСП
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = []
    
    for item in ldsp_items:
        price_text = f"{item.unit_price}₽/{item.price_unit}" if item.unit_price else "Цена не указана"
        button_text = f"{item.name} — {price_text}"
        # Ограничиваем длину текста кнопки
        if len(button_text) > 50:
            button_text = f"{item.name[:30]}... — {price_text}"
        
        keyboard.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"ldsp:{item.id}"
            )
        ])

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await message.answer(
        "🏠 Выберите ЛДСП для корпуса:",
        reply_markup=markup
    )


@router.callback_query(F.data.startswith("ldsp:"))
async def process_ldsp_selection(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор ЛДСП."""
    # Проверяем, что пользователь существует
    if not callback.from_user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    ldsp_id = int(callback.data.split(":")[1])

    # Получаем calculation_id из глобального словаря (приоритет) или состояния FSM
    data = await state.get_data()
    calculation_id = data.get('calculation_id')

    if not calculation_id:
        await callback.answer("Ошибка: не найден ID расчёта")
        return

    # Сохраняем calculation_id в состоянии для будущих вызовов
    await state.update_data(calculation_id=calculation_id)
    
    # Получаем данные о выбранном ЛДСП
    async for session in get_session():
        ldsp_item = await session.get(PriceItem, ldsp_id)
        if not ldsp_item:
            await callback.answer("Ошибка: ЛДСП не найдено")
            return
        break

    # Сохраняем calculation_id и выбор ЛДСП в состоянии
    data = await state.get_data()
    selected_materials = data.get('selected_materials', {})
    selected_materials['ldsp'] = {
        'id': ldsp_item.id,
        'name': ldsp_item.name,
        'price': ldsp_item.unit_price,
        'price_unit': ldsp_item.price_unit,
        'thickness_mm': ldsp_item.thickness_mm,
        'brand': ldsp_item.brand
    }
    await state.update_data(
        calculation_id=calculation_id,
        selected_materials=selected_materials
    )
    
    await callback.message.answer(
        f"✅ Выбрано ЛДСП: {ldsp_item.name}\n"
        f"Цена: {ldsp_item.unit_price}₽/{ldsp_item.price_unit}"
    )
    
    # Переходим к выбору фасадов
    await select_facade_category(callback.message, state)
    await callback.answer()


async def select_facade_category(message: Message, state: FSMContext):
    """Показывает выбор категории фасадов."""
    # Получаем доступные категории фасадов из базы
    async for session in get_session():
        from sqlalchemy import select
        result = await session.execute(
            select(PriceItem.category).distinct().where(
                PriceItem.category.in_([
                    'МДФ плиты', 'ЭФТРИ ПВХ', 'Лакокраска', 'Алюминиевый фасад', 'Фасады'
                ])
            )
        )
        available_categories = [row[0] for row in result.all()]
        break

    if not available_categories:
        await message.answer("❌ В прайсе не найдены категории фасадов.")
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard = []
    category_names = {
        'МДФ плиты': 'МДФ плиты',
        'ЭФТРИ ПВХ': 'ЭФТРИ ПВХ', 
        'Лакокраска': 'Лакокраска',
        'Алюминиевый фасад': 'Алюминиевый фасад',
        'Фасады': 'Другие фасады'
    }
    
    for category in available_categories:
        display_name = category_names.get(category, category)
        keyboard.append([
            InlineKeyboardButton(text=display_name, callback_data=f"facade_cat:{category}")
        ])
    
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await message.answer(
        "🚪 Выберите категорию фасадов:",
        reply_markup=markup
    )


@router.callback_query(F.data.startswith("facade_cat:"))
async def process_facade_category(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор категории фасадов."""
    # Проверяем, что пользователь существует
    if not callback.from_user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    category = callback.data.split(":", 1)[1]

    # Получаем calculation_id из глобального словаря (приоритет) или состояния FSM
    data = await state.get_data()
    calculation_id = data.get('calculation_id')

    if not calculation_id:
        await callback.answer("Ошибка: не найден ID расчёта")
        return
    
    # Получаем фасады выбранной категории
    async for session in get_session():
        result = await session.execute(
            select(PriceItem).where(
                PriceItem.category == category,
                PriceItem.is_active == 1
            ).order_by(PriceItem.name)
        )
        facade_items = result.scalars().all()
        break

    if not facade_items:
        await callback.message.answer(f"❌ В категории '{category}' нет доступных позиций.")
        return

    # Сохраняем calculation_id в состоянии
    await state.update_data(calculation_id=calculation_id)
    
    # Создаём клавиатуру с фасадами
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = []
    
    for item in facade_items:
        price_text = f"{item.unit_price}₽/{item.price_unit}" if item.unit_price else "Цена не указана"
        button_text = f"{item.name} — {price_text}"
        # Ограничиваем длину текста кнопки
        if len(button_text) > 50:
            button_text = f"{item.name[:30]}... — {price_text}"
        
        keyboard.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"facade:{item.id}"
            )
        ])

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await callback.message.answer(
        f"Выберите фасад из категории '{category}':",
        reply_markup=markup
    )
    
    await callback.answer()


@router.callback_query(F.data.startswith("facade:"))
async def process_facade_selection(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор фасада."""
    # Проверяем, что пользователь существует
    if not callback.from_user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    facade_id = int(callback.data.split(":")[1])

    # Получаем calculation_id из глобального словаря (приоритет) или состояния FSM
    data = await state.get_data()
    calculation_id = data.get('calculation_id')

    if not calculation_id:
        await callback.answer("Ошибка: не найден ID расчёта")
        return
    
    # Получаем данные о выбранном фасаде
    async for session in get_session():
        facade_item = await session.get(PriceItem, facade_id)
        if not facade_item:
            await callback.answer("Ошибка: Фасад не найден")
            return
        break

    # Сохраняем calculation_id в состоянии
    await state.update_data(calculation_id=calculation_id)
    
    # Проверяем, требуется ли ручной ввод цены
    if facade_item.requires_manual_price:
        # Сохраняем временно выбранный фасад и переходим к вводу цены
        await state.update_data(temp_facade_id=facade_id)
        await state.set_state(MaterialSelectionStates.waiting_for_manual_price)
        await callback.message.answer(
            f"Для фасада '{facade_item.name}' требуется ручной ввод цены.\n"
            f"Введите цену за {facade_item.price_unit}:"
        )
        await callback.answer()
        return

    # Сохраняем выбор фасада
    await save_facade_selection(callback, state, facade_item, facade_item.unit_price)
    await callback.answer()


async def save_facade_selection(callback: CallbackQuery, state: FSMContext, facade_item: PriceItem, price: float):
    """Сохраняет выбор фасада."""
    data = await state.get_data()
    selected_materials = data.get('selected_materials', {})
    selected_materials['facades'] = {
        'id': facade_item.id,
        'name': facade_item.name,
        'price': price,
        'price_unit': facade_item.price_unit,
        'thickness_mm': facade_item.thickness_mm,
        'category': facade_item.category,
        'brand': facade_item.brand,
        # P1-05: формат листа нужен, если цена за лист, а расход — м².
        'sheet_width_mm': facade_item.sheet_width_mm,
        'sheet_height_mm': facade_item.sheet_height_mm,
    }
    await state.update_data(selected_materials=selected_materials)
    
    await callback.message.answer(
        f"✅ Выбран фасад: {facade_item.name}\n"
        f"Цена: {price}₽/{facade_item.price_unit}"
    )
    
    # Автоматически подбираем кромку по производителю ЛДСП
    await select_edge_automatically(callback.message, state)


@router.message(MaterialSelectionStates.waiting_for_manual_price)
async def process_manual_price(message: Message, state: FSMContext):
    """Обрабатывает ручной ввод цены."""
    try:
        price = float(message.text.strip().replace(',', '.'))
        if price < 0:
            raise ValueError("Цена должна быть положительной")
    except ValueError:
        await message.answer("Введите корректную цену (положительное число):")
        return
    # Получаем calculation_id из состояния
    data = await state.get_data()
    calculation_id = data.get('calculation_id')
    
    if not calculation_id:
        await message.answer("Ошибка: не найден ID расчёта")
        return
    
    # Получаем временные данные
    selected_materials = data.get('selected_materials', {})
    selected_hardware = data.get('selected_hardware', {})
    
    # Сохраняем calculation_id в состоянии
    await state.update_data(calculation_id=calculation_id)
    
    # Определяем, для чего вводится цена
    if 'temp_facade_id' in data:
        # Ручной ввод цены для фасада
        facade_id = data['temp_facade_id']
        async for session in get_session():
            facade_item = await session.get(PriceItem, facade_id)
            break
        
        selected_materials['facades'] = {
            'id': facade_item.id,
            'name': facade_item.name,
            'price': price,
            'price_unit': facade_item.price_unit,
            'thickness_mm': facade_item.thickness_mm,
            'category': facade_item.category,
            'brand': facade_item.brand,
            'manual_price': True,
            'sheet_width_mm': facade_item.sheet_width_mm,
            'sheet_height_mm': facade_item.sheet_height_mm,
        }
        await state.update_data(selected_materials=selected_materials, temp_facade_id=None)
        
        await message.answer(
            f"✅ Выбран фасад: {facade_item.name}\n"
            f"Цена: {price}₽/{facade_item.price_unit} (введена вручную)"
        )
        
        # Переходим к кромке
        await select_edge_automatically(message, state)
        
    elif 'temp_edge_id' in data:
        # Ручной ввод цены для кромки
        edge_id = data['temp_edge_id']
        async for session in get_session():
            edge_item = await session.get(PriceItem, edge_id)
            break
        
        selected_materials['edge'] = {
            'id': edge_item.id,
            'name': edge_item.name,
            'price': price,
            'price_unit': edge_item.price_unit,
            'thickness_mm': edge_item.thickness_mm,
            'brand': edge_item.brand,
            'manual_price': True
        }
        await state.update_data(selected_materials=selected_materials, temp_edge_id=None)
        
        await message.answer(
            f"✅ Выбрана кромка: {edge_item.name}\n"
            f"Цена: {price}₽/{edge_item.price_unit} (введена вручную)"
        )
        
        # Переходим к петлям
        await select_hinge_brand(message, state)
        
    elif 'temp_hinge_id' in data:
        # Ручной ввод цены для петель
        hinge_id = data['temp_hinge_id']
        async for session in get_session():
            hinge_item = await session.get(PriceItem, hinge_id)
            break
        
        selected_hardware['hinges'] = {
            'id': hinge_item.id,
            'name': hinge_item.name,
            'price': price,
            'price_unit': hinge_item.price_unit,
            'brand': hinge_item.brand,
            'manual_price': True
        }
        await state.update_data(selected_hardware=selected_hardware, temp_hinge_id=None)
        
        await message.answer(
            f"✅ Выбраны петли: {hinge_item.name}\n"
            f"Цена: {price}₽/{hinge_item.price_unit} (введена вручную)"
        )
        
        # Проверяем ящики
        await check_for_drawers(message, state)
        
    elif 'temp_drawer_id' in data:
        # Ручной ввод цены для ящиков
        drawer_id = data['temp_drawer_id']
        async for session in get_session():
            drawer_item = await session.get(PriceItem, drawer_id)
            break
        
        selected_hardware['drawers'] = {
            'id': drawer_item.id,
            'name': drawer_item.name,
            'price': price,
            'price_unit': drawer_item.price_unit,
            'brand': drawer_item.brand,
            'manual_price': True
        }
        await state.update_data(selected_hardware=selected_hardware, temp_drawer_id=None)
        
        await message.answer(
            f"✅ Выбраны ящики: {drawer_item.name}\n"
            f"Цена: {price}₽/{drawer_item.price_unit} (введена вручную)"
        )
        
        # Переходим к выбору ручки (P0-02), затем завершение
        await select_handle(message, state)
    
    await state.set_state(None)


async def select_edge_automatically(message: Message, state: FSMContext):
    """Автоматически подбирает кромку по производителю ЛДСП."""
    data = await state.get_data()
    selected_materials = data.get('selected_materials', {})
    ldsp_brand = selected_materials.get('ldsp', {}).get('brand')
    
    if not ldsp_brand:
        await message.answer("❌ Ошибка: ЛДСП не выбран")
        return

    # Ищем кромку по бренду ЛДСП: сначала с ценой и толщиной 0.8 мм,
    # затем любую с ценой, затем любую по бренду (может потребовать цену).
    async for session in get_session():
        base = select(PriceItem).where(
            PriceItem.category == "Кромка",
            PriceItem.is_active == 1,
        )
        result = await session.execute(
            base.where(
                PriceItem.brand == ldsp_brand,
                PriceItem.unit_price.is_not(None),
                PriceItem.thickness_mm == 0.8,
            ).order_by(PriceItem.name)
        )
        edge_items = result.scalars().all()
        if not edge_items:
            result = await session.execute(
                base.where(
                    PriceItem.brand == ldsp_brand,
                    PriceItem.unit_price.is_not(None),
                ).order_by(PriceItem.name)
            )
            edge_items = result.scalars().all()
        if not edge_items:
            result = await session.execute(
                base.where(PriceItem.unit_price.is_not(None)).order_by(PriceItem.name)
            )
            edge_items = result.scalars().all()
        if not edge_items:
            result = await session.execute(
                base.where(PriceItem.brand == ldsp_brand).order_by(PriceItem.name)
            )
            edge_items = result.scalars().all()
        break

    if not edge_items:
        await message.answer("❌ В прайсе не найдена кромка. Обратитесь к администратору.")
        return

    # Берем первую подходящую кромку
    edge_item = edge_items[0]
    
    # Проверяем ручной ввод цены
    if edge_item.requires_manual_price:
        await state.update_data(temp_edge_id=edge_item.id)
        await state.set_state(MaterialSelectionStates.waiting_for_manual_price)
        await message.answer(
            f"Для кромки '{edge_item.name}' требуется ручной ввод цены.\n"
            f"Введите цену за {edge_item.price_unit}:"
        )
        return

    # Сохраняем выбор кромки
    selected_materials['edge'] = {
        'id': edge_item.id,
        'name': edge_item.name,
        'price': edge_item.unit_price,
        'price_unit': edge_item.price_unit,
        'thickness_mm': edge_item.thickness_mm,
        'brand': edge_item.brand
    }
    await state.update_data(selected_materials=selected_materials)
    
    await message.answer(
        f"✅ Автоматически подобрана кромка: {edge_item.name}\n"
        f"Цена: {edge_item.unit_price}₽/{edge_item.price_unit}\n"
        f"(по производителю ЛДСП: {ldsp_brand})"
    )
    
    # Переходим к выбору петель
    await select_hinge_brand(message, state)


async def select_hinge_brand(message: Message, state: FSMContext):
    """Показывает выбор бренда петель."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard = [
        [InlineKeyboardButton(text="BOYARD", callback_data="hinge_brand:BOYARD")],
        [InlineKeyboardButton(text="BLUM", callback_data="hinge_brand:BLUM")],
        [InlineKeyboardButton(text="HETTICH", callback_data="hinge_brand:HETTICH")]
    ]
    
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await message.answer(
        "🔧 Выберите бренд петель:",
        reply_markup=markup
    )


@router.callback_query(F.data.startswith("hinge_brand:"))
async def process_hinge_brand(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор бренда петель."""
    # Проверяем, что пользователь существует
    if not callback.from_user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    brand = callback.data.split(":", 1)[1]

    # Получаем calculation_id из глобального словаря (приоритет) или состояния FSM
    data = await state.get_data()
    calculation_id = data.get('calculation_id')

    if not calculation_id:
        await callback.answer("Ошибка: не найден ID расчёта")
        return
    
    # Получаем петли выбранного бренда
    async for session in get_session():
        result = await session.execute(
            select(PriceItem).where(
                PriceItem.category == "Петли",
                PriceItem.brand == brand,
                PriceItem.is_active == 1
            ).order_by(PriceItem.name)
        )
        hinge_items = result.scalars().all()
        break

    if not hinge_items:
        await callback.message.answer(f"❌ Петли бренда '{brand}' не найдены в прайсе.")
        return

    # Сохраняем calculation_id в состоянии
    await state.update_data(calculation_id=calculation_id)
    
    # Создаём клавиатуру с петлями
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = []
    
    for item in hinge_items:
        price_text = f"{item.unit_price}₽/{item.price_unit}" if item.unit_price else "Цена не указана"
        button_text = f"{item.name} — {price_text}"
        # Ограничиваем длину текста кнопки
        if len(button_text) > 50:
            button_text = f"{item.name[:30]}... — {price_text}"
        
        keyboard.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"hinge:{item.id}"
            )
        ])

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await callback.message.answer(
        f"Выберите модель петель {brand}:",
        reply_markup=markup
    )
    
    await callback.answer()


@router.callback_query(F.data.startswith("hinge:"))
async def process_hinge_selection(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор петель."""
    # Проверяем, что пользователь существует
    if not callback.from_user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    hinge_id = int(callback.data.split(":")[1])

    # Получаем calculation_id из глобального словаря (приоритет) или состояния FSM
    data = await state.get_data()
    calculation_id = data.get('calculation_id')

    if not calculation_id:
        await callback.answer("Ошибка: не найден ID расчёта")
        return
    
    # Получаем данные о выбранных петлях
    async for session in get_session():
        hinge_item = await session.get(PriceItem, hinge_id)
        if not hinge_item:
            await callback.answer("Ошибка: Петли не найдены")
            return
        break

    # Сохраняем calculation_id в состоянии
    await state.update_data(calculation_id=calculation_id)
    
    # Проверяем ручной ввод цены
    if hinge_item.requires_manual_price:
        await state.update_data(temp_hinge_id=hinge_id)
        await state.set_state(MaterialSelectionStates.waiting_for_manual_price)
        await callback.message.answer(
            f"Для петель '{hinge_item.name}' требуется ручной ввод цены.\n"
            f"Введите цену за {hinge_item.price_unit}:"
        )
        await callback.answer()
        return

    # Сохраняем выбор петель
    data = await state.get_data()
    selected_hardware = data.get('selected_hardware', {})
    selected_hardware['hinges'] = {
        'id': hinge_item.id,
        'name': hinge_item.name,
        'price': hinge_item.unit_price,
        'price_unit': hinge_item.price_unit,
        'brand': hinge_item.brand
    }
    await state.update_data(selected_hardware=selected_hardware)
    
    await callback.message.answer(
        f"✅ Выбраны петли: {hinge_item.name}\n"
        f"Цена: {hinge_item.unit_price}₽/{hinge_item.price_unit}"
    )
    
    # Проверяем, есть ли ящики в модулях
    await check_for_drawers(callback.message, state)
    await callback.answer()


async def check_for_drawers(message: Message, state: FSMContext):
    """Проверяет, есть ли ящики в модулях и предлагает выбрать систему ящиков."""
    data = await state.get_data()
    calculation_id = data.get('calculation_id')
    
    if not calculation_id:
        await message.answer("❌ Ошибка: расчет не найден")
        return

    # Получаем модули из расчета (только свой — P1-14)
    if not message.from_user:
        await message.answer("❌ Ошибка: пользователь не найден")
        return
    async for session in get_session():
        calculation = await get_owned_calculation(
            session, calculation_id, message.from_user.id
        )
        if not calculation or not calculation.modules:
            await message.answer("❌ Ошибка: модули не найдены")
            return
        
        has_drawers = any(
            module.get('filling') == 'drawers' 
            for module in calculation.modules
        )
        break

    if has_drawers:
        # Есть ящики, предлагаем выбрать систему
        await select_drawer_brand(message, state)
    else:
        # Ящиков нет — переходим к выбору ручки
        await select_handle(message, state)


async def select_drawer_brand(message: Message, state: FSMContext):
    """Показывает выбор бренда системы ящиков."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard = [
        [InlineKeyboardButton(text="BOYARD СТАРТ", callback_data="drawer_brand:BOYARD СТАРТ")],
        [InlineKeyboardButton(text="HETTICH", callback_data="drawer_brand:HETTICH")],
        [InlineKeyboardButton(text="BLUM TANDEMBOX", callback_data="drawer_brand:BLUM TANDEMBOX")],
        [InlineKeyboardButton(text="BLUM LEGRABOX", callback_data="drawer_brand:BLUM LEGRABOX")]
    ]
    
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await message.answer(
        "📦 Выберите систему ящиков:",
        reply_markup=markup
    )


@router.callback_query(F.data.startswith("drawer_brand:"))
async def process_drawer_brand(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор бренда ящиков."""
    # Проверяем, что пользователь существует
    if not callback.from_user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    brand = callback.data.split(":", 1)[1]

    # Получаем calculation_id из глобального словаря (приоритет) или состояния FSM
    data = await state.get_data()
    calculation_id = data.get('calculation_id')

    if not calculation_id:
        await callback.answer("Ошибка: не найден ID расчёта")
        return
    
    # Получаем ящики выбранного бренда
    async for session in get_session():
        result = await session.execute(
            select(PriceItem).where(
                PriceItem.category == "Ящики",
                PriceItem.brand == brand,
                PriceItem.is_active == 1
            ).order_by(PriceItem.name)
        )
        drawer_items = result.scalars().all()
        break

    if not drawer_items:
        await callback.message.answer(f"❌ Ящики '{brand}' не найдены в прайсе.")
        # Продолжаем без ящиков — к выбору ручки
        await select_handle(callback.message, state)
        return

    # Сохраняем calculation_id в состоянии
    await state.update_data(calculation_id=calculation_id)
    
    # Создаём клавиатуру с ящиками
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = []
    
    for item in drawer_items:
        price_text = f"{item.unit_price}₽/{item.price_unit}" if item.unit_price else "Цена не указана"
        button_text = f"{item.name} — {price_text}"
        # Ограничиваем длину текста кнопки
        if len(button_text) > 50:
            button_text = f"{item.name[:30]}... — {price_text}"
        
        keyboard.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"drawer:{item.id}"
            )
        ])

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await callback.message.answer(
        f"Выберите модель ящиков {brand}:",
        reply_markup=markup
    )
    
    await callback.answer()


@router.callback_query(F.data.startswith("drawer:"))
async def process_drawer_selection(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор ящиков."""
    # Проверяем, что пользователь существует
    if not callback.from_user:
        await callback.answer("Ошибка: пользователь не найден")
        return

    drawer_id = int(callback.data.split(":")[1])

    # Получаем calculation_id из глобального словаря (приоритет) или состояния FSM
    data = await state.get_data()
    calculation_id = data.get('calculation_id')

    if not calculation_id:
        await callback.answer("Ошибка: не найден ID расчёта")
        return
    
    # Получаем данные о выбранных ящиках
    async for session in get_session():
        drawer_item = await session.get(PriceItem, drawer_id)
        if not drawer_item:
            await callback.answer("Ошибка: Ящики не найдены")
            return
        break

    # Сохраняем calculation_id в состоянии
    await state.update_data(calculation_id=calculation_id)
    
    # Проверяем ручной ввод цены
    if drawer_item.requires_manual_price:
        await state.update_data(temp_drawer_id=drawer_id)
        await state.set_state(MaterialSelectionStates.waiting_for_manual_price)
        await callback.message.answer(
            f"Для ящиков '{drawer_item.name}' требуется ручной ввод цены.\n"
            f"Введите цену за {drawer_item.price_unit}:"
        )
        await callback.answer()
        return

    # Сохраняем выбор ящиков
    data = await state.get_data()
    selected_hardware = data.get('selected_hardware', {})
    selected_hardware['drawers'] = {
        'id': drawer_item.id,
        'name': drawer_item.name,
        'price': drawer_item.unit_price,
        'price_unit': drawer_item.price_unit,
        'brand': drawer_item.brand
    }
    await state.update_data(selected_hardware=selected_hardware)
    
    await callback.message.answer(
        f"✅ Выбраны ящики: {drawer_item.name}\n"
        f"Цена: {drawer_item.unit_price}₽/{drawer_item.price_unit}"
    )
    
    # Переходим к выбору ручки, затем завершение
    await select_handle(callback.message, state)
    await callback.answer()


async def select_handle(message: Message, state: FSMContext):
    """Шаг выбора ручки (P0-02): без цены ручек КП блокируется."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    items = []
    async for session in get_session():
        result = await session.execute(
            select(PriceItem).where(
                PriceItem.category == "Ручки",
                PriceItem.is_active == 1,
            ).order_by(PriceItem.name)
        )
        items = result.scalars().all()
        break

    if not items:
        await message.answer(
            "⚠️ В прайсе нет ручек. Смета будет без стоимости ручек — "
            "КП будет заблокировано до добавления цены."
        )
        await finish_material_selection(message, state)
        return

    keyboard = []
    for item in items[:40]:
        price_text = f"{item.unit_price:.0f}₽" if item.unit_price else "нет цены"
        keyboard.append([
            InlineKeyboardButton(
                text=f"{item.name[:45]} — {price_text}",
                callback_data=f"handle:{item.id}",
            )
        ])
    keyboard.append([
        InlineKeyboardButton(text="Без ручек (Gola/push)", callback_data="handle:none")
    ])

    await message.answer(
        "🖐 Выберите ручку (или «Без ручек», если проект на Gola/push-to-open):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


@router.callback_query(F.data.startswith("handle:"))
async def process_handle_selection(callback: CallbackQuery, state: FSMContext):
    """Сохранить выбранную ручку и завершить выбор материалов."""
    value = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected_hardware = data.get("selected_hardware", {})

    if value == "none":
        selected_hardware.pop("handles", None)
        await state.update_data(selected_hardware=selected_hardware)
        await callback.message.answer("Ручки: без накладных ручек")
    else:
        try:
            handle_id = int(value)
        except ValueError:
            await callback.answer("Некорректная ручка", show_alert=True)
            return
        async for session in get_session():
            item = await session.get(PriceItem, handle_id)
            if item is None:
                await callback.answer("Ручка не найдена", show_alert=True)
                return
            if item.requires_manual_price or not item.unit_price:
                await callback.answer("У ручки нет цены — выберите другую", show_alert=True)
                return
            selected_hardware["handles"] = {
                "id": item.id,
                "name": item.name,
                "price": item.unit_price,
                "price_unit": item.price_unit,
                "brand": item.brand,
            }
            await state.update_data(selected_hardware=selected_hardware)
            break
        await callback.message.answer(f"✅ Выбрана ручка: {item.name}")

    await finish_material_selection(callback.message, state)
    await callback.answer()


async def finish_material_selection(message: Message, state: FSMContext):
    """Завершает выбор материалов и сохраняет в базу данных."""
    data = await state.get_data()
    calculation_id = data.get('calculation_id')
    selected_materials = data.get('selected_materials', {})
    selected_hardware = data.get('selected_hardware', {})
    
    if not calculation_id or not message.from_user:
        await message.answer("❌ Ошибка: расчет не найден")
        return

    # Сохраняем выбранные материалы в базу данных (только свой — P1-14)
    async for session in get_session():
        calculation = await get_owned_calculation(
            session, calculation_id, message.from_user.id
        )
        if calculation:
            calculation.selected_materials = selected_materials
            calculation.selected_hardware = selected_hardware
            await session.commit()
        break

    # await state.clear()  # Не очищаем состояние, чтобы сохранить calculation_id
    
    # Показываем резюме выбранных материалов
    summary = "✅ Выбор материалов завершён!\n\n"
    
    if 'ldsp' in selected_materials:
        ldsp = selected_materials['ldsp']
        summary += f"🏠 ЛДСП: {ldsp['name']} ({ldsp['price']}₽)\n"
    
    if 'facades' in selected_materials:
        facades = selected_materials['facades']
        summary += f"🚪 Фасады: {facades['name']} ({facades['price']}₽/{facades.get('category', '')})\n"
    
    if 'edge' in selected_materials:
        edge = selected_materials['edge']
        summary += f"📏 Кромка: {edge['name']} ({edge['price']}₽)\n"
    
    if 'hinges' in selected_hardware:
        hinges = selected_hardware['hinges']
        summary += f"🔧 Петли: {hinges['name']} ({hinges['price']}₽)\n"
    
    if 'drawers' in selected_hardware:
        drawers = selected_hardware['drawers']
        summary += f"📦 Ящики: {drawers['name']} ({drawers['price']}₽)\n"

    if 'handles' in selected_hardware:
        handles = selected_hardware['handles']
        summary += f"🖐 Ручки: {handles['name']} ({handles['price']}₽)\n"
    else:
        summary += "🖐 Ручки: не выбраны (КП будет заблокировано)\n"

    summary += "\nТеперь можно запустить расчёт стоимости!"

    # Клавиатура для запуска расчёта
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧮 Рассчитать стоимость", callback_data=f"calculate_cost:{calculation_id}")]
        ]
    )

    await message.answer(summary, reply_markup=keyboard)
