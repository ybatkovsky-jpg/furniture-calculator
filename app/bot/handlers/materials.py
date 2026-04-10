"""
Обработчики для выбора материалов из прайса.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from app.bot.states.materials import MaterialSelectionStates
from app.db.session import get_session
from app.models.price import PriceItem
from app.models.calculation import Calculation

router = Router()


async def start_material_selection(message: Message, calculation_id: int):
    """Начинает процесс выбора материалов."""
    # Сохраняем calculation_id в состоянии
    await message.bot.get('fsm_storage').update_data(
        user=message.from_user.id,
        chat=message.chat.id,
        data={'calculation_id': calculation_id, 'selected_materials': {}, 'selected_hardware': {}}
    )
    
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
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
    ldsp_id = int(callback.data.split(":")[1])
    
    # Получаем данные о выбранном ЛДСП
    async for session in get_session():
        ldsp_item = await session.get(PriceItem, ldsp_id)
        if not ldsp_item:
            await callback.answer("Ошибка: ЛДСП не найдено")
            return
        break

    # Сохраняем выбор ЛДСП
    data = await state.get_data()
    selected_materials = data.get('selected_materials', {})
    selected_materials['ldsp'] = {
        'id': ldsp_item.id,
        'name': ldsp_item.name,
        'price': ldsp_item.unit_price,
        'brand': ldsp_item.brand
    }
    await state.update_data(selected_materials=selected_materials)
    
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

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
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
    category = callback.data.split(":", 1)[1]
    
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

    # Создаём клавиатуру с фасадами
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
    facade_id = int(callback.data.split(":")[1])
    
    # Получаем данные о выбранном фасаде
    async for session in get_session():
        facade_item = await session.get(PriceItem, facade_id)
        if not facade_item:
            await callback.answer("Ошибка: Фасад не найден")
            return
        break

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
        'category': facade_item.category,
        'brand': facade_item.brand
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

    # Получаем временные данные
    data = await state.get_data()
    selected_materials = data.get('selected_materials', {})
    selected_hardware = data.get('selected_hardware', {})
    
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
            'category': facade_item.category,
            'brand': facade_item.brand,
            'manual_price': True
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
            'brand': drawer_item.brand,
            'manual_price': True
        }
        await state.update_data(selected_hardware=selected_hardware, temp_drawer_id=None)
        
        await message.answer(
            f"✅ Выбраны ящики: {drawer_item.name}\n"
            f"Цена: {price}₽/{drawer_item.price_unit} (введена вручную)"
        )
        
        # Завершаем выбор материалов
        await finish_material_selection(message, state)
    
    await state.set_state(None)


async def select_edge_automatically(message: Message, state: FSMContext):
    """Автоматически подбирает кромку по производителю ЛДСП."""
    data = await state.get_data()
    selected_materials = data.get('selected_materials', {})
    ldsp_brand = selected_materials.get('ldsp', {}).get('brand')
    
    if not ldsp_brand:
        await message.answer("❌ Ошибка: ЛДСП не выбран")
        return

    # Ищем кромку по бренду ЛДСП
    async for session in get_session():
        result = await session.execute(
            select(PriceItem).where(
                PriceItem.category == "Кромка",
                PriceItem.brand == ldsp_brand,
                PriceItem.is_active == 1
            ).order_by(PriceItem.name)
        )
        edge_items = result.scalars().all()
        break

    if not edge_items:
        # Если не нашли по бренду, берём любую кромку
        async for session in get_session():
            result = await session.execute(
                select(PriceItem).where(
                    PriceItem.category == "Кромка",
                    PriceItem.is_active == 1
                ).order_by(PriceItem.name)
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
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
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
    brand = callback.data.split(":", 1)[1]
    
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

    # Создаём клавиатуру с петлями
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
    hinge_id = int(callback.data.split(":")[1])
    
    # Получаем данные о выбранных петлях
    async for session in get_session():
        hinge_item = await session.get(PriceItem, hinge_id)
        if not hinge_item:
            await callback.answer("Ошибка: Петли не найдены")
            return
        break

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

    # Получаем модули из расчета
    async for session in get_session():
        calculation = await session.get(Calculation, calculation_id)
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
        # Ящиков нет, завершаем выбор материалов
        await finish_material_selection(message, state)


async def select_drawer_brand(message: Message, state: FSMContext):
    """Показывает выбор бренда системы ящиков."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
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
    brand = callback.data.split(":", 1)[1]
    
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
        # Продолжаем без ящиков
        await finish_material_selection(callback.message, state)
        return

    # Создаём клавиатуру с ящиками
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
    drawer_id = int(callback.data.split(":")[1])
    
    # Получаем данные о выбранных ящиках
    async for session in get_session():
        drawer_item = await session.get(PriceItem, drawer_id)
        if not drawer_item:
            await callback.answer("Ошибка: Ящики не найдены")
            return
        break

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
        'brand': drawer_item.brand
    }
    await state.update_data(selected_hardware=selected_hardware)
    
    await callback.message.answer(
        f"✅ Выбраны ящики: {drawer_item.name}\n"
        f"Цена: {drawer_item.unit_price}₽/{drawer_item.price_unit}"
    )
    
    # Завершаем выбор материалов
    await finish_material_selection(callback.message, state)
    await callback.answer()


async def finish_material_selection(message: Message, state: FSMContext):
    """Завершает выбор материалов и сохраняет в базу данных."""
    data = await state.get_data()
    calculation_id = data.get('calculation_id')
    selected_materials = data.get('selected_materials', {})
    selected_hardware = data.get('selected_hardware', {})
    
    if not calculation_id:
        await message.answer("❌ Ошибка: расчет не найден")
        return

    # Сохраняем выбранные материалы в базу данных
    async for session in get_session():
        calculation = await session.get(Calculation, calculation_id)
        if calculation:
            calculation.selected_materials = selected_materials
            calculation.selected_hardware = selected_hardware
            await session.commit()
        break

    await state.clear()
    
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
    
    summary += "\nТеперь можно запустить расчёт стоимости!"
    
    await message.answer(summary)
