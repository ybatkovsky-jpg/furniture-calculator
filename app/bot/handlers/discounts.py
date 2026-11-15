"""
Обработчик скидок/наценок/бонуса дизайнера.
"""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.bot.handlers.calculation import (
    _extract_discount_info,
    _format_estimate_text,
    show_estimate,
)
from app.db.session import get_session
from app.services.access import get_owned_calculation
from app.services.calc_engine import calculate_full_cost
from app.services.cost_calc import DiscountInfo, calculate_cost
from app.services.pricing_policy import load_default_policy

router = Router()


class DiscountState(StatesGroup):
    entering_value = State()


@router.callback_query(F.data.startswith("discount_menu:"))
async def show_discount_menu(callback: CallbackQuery):
    """Показать меню скидок/наценок."""
    parts = callback.data.split(":")
    if len(parts) > 1:
        calculation_id = int(parts[1])
    else:
        await callback.answer("Ошибка: расчёт не найден.")
        return

    async for session in get_session():
        calculation = await get_owned_calculation(
            session, calculation_id, callback.from_user.id
        )
        if not calculation:
            await callback.message.answer("Ошибка: расчёт не найден.")
            await callback.answer()
            return

        price = calculation.final_price_cash or calculation.total_base_cash or 0
        text = (
            "💰 Скидка / наценка / бонус\n\n"
            f"Текущая цена наличка: {price:,.0f} ₽\n"
            f"Бонус дизайнера: {'✅ ВКЛ' if calculation.designer_bonus_enabled else '❌ ВЫКЛ'}\n\n"
            "Выберите действие:"
        )

        from app.bot.keyboards.inline import get_discount_keyboard
        keyboard = get_discount_keyboard(calculation)
        await callback.message.edit_text(text, reply_markup=keyboard)
        break

    await callback.answer()


@router.callback_query(F.data.startswith("set_discount_"))
async def start_discount_input(callback: CallbackQuery, state: FSMContext):
    """Начинает ввод значения скидки/наценки."""
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer("Ошибка: неверный формат данных")
        return

    discount_type_raw = parts[0]
    calculation_id = int(parts[1])

    # Извлекаем тип скидки
    if discount_type_raw.startswith("set_discount_"):
        discount_type = discount_type_raw.replace("set_discount_", "")
    else:
        await callback.answer("Ошибка: неверный тип скидки")
        return

    # P1-14: менять скидку можно только в своём расчёте.
    async for session in get_session():
        if not await get_owned_calculation(
            session, calculation_id, callback.from_user.id
        ):
            await callback.answer("Расчёт не найден", show_alert=True)
            return
        break

    await state.update_data(discount_type=discount_type, calculation_id=calculation_id)
    await state.set_state(DiscountState.entering_value)

    labels = {
        "percent": "Введите процент скидки (например: 5)",
        "fixed": "Введите сумму скидки в рублях (например: 10000)",
        "markup_percent": "Введите процент наценки (например: 10)",
        "markup_fixed": "Введите сумму наценки в рублях (например: 5000)",
    }
    await callback.message.answer(labels.get(discount_type, "Введите значение:"))


@router.message(DiscountState.entering_value)
async def process_discount_value(message: Message, state: FSMContext):
    """Обрабатывает введённое значение скидки/наценки."""
    try:
        value = float(message.text.replace(",", ".").replace(" ", ""))
        if value < 0:
            raise ValueError("Значение должно быть положительным")
    except ValueError:
        await message.answer("Введите корректное положительное число:")
        return

    data = await state.get_data()
    discount_type = data.get("discount_type")
    calculation_id = data.get("calculation_id")

    if not calculation_id or not message.from_user:
        await message.answer("Ошибка: расчёт не найден")
        return

    # Обновляем расчёт в БД (только свой — P1-14)
    async for session in get_session():
        calculation = await get_owned_calculation(
            session, calculation_id, message.from_user.id
        )
        if not calculation:
            await message.answer("Ошибка: расчёт не найден")
            return

        # P0-08: проверяем границы до записи в БД, а не после расчёта.
        base = calculation.total_base_cash or calculation.final_price_cash or 0
        if discount_type == "percent" and value > 100:
            await message.answer("❌ Скидка не может быть больше 100%.")
            return
        if discount_type == "markup_percent" and value > 100:
            await message.answer("❌ Наценка не может быть больше 100%.")
            return
        if discount_type == "fixed" and value > base:
            await message.answer(
                f"❌ Скидка {value:,.0f} ₽ больше суммы {base:,.0f} ₽."
            )
            return

        # Сбрасываем предыдущие скидки/наценки
        calculation.discount_type = None
        calculation.discount_value = 0
        calculation.markup_type = None
        calculation.markup_value = 0

        # Устанавливаем новые значения
        if discount_type in ["percent", "fixed"]:
            calculation.discount_type = discount_type
            calculation.discount_value = value
        elif discount_type in ["markup_percent", "markup_fixed"]:
            calculation.markup_type = discount_type.replace("markup_", "")
            calculation.markup_value = value

        # Пересчитываем цены
        discount_info = DiscountInfo(
            discount_type=calculation.discount_type,
            discount_value=calculation.discount_value,
            markup_type=calculation.markup_type,
            markup_value=calculation.markup_value,
            designer_bonus_enabled=bool(calculation.designer_bonus_enabled),
            designer_bonus_rate=calculation.designer_bonus_rate or 0.10
        )

        # Получаем текущие стоимости
        material_cost = calculation.material_cost or 0
        edge_cost = calculation.edge_cost or 0
        facade_cost = calculation.facade_cost or 0
        hardware_cost = calculation.hardware_cost or 0
        glass_cost = calculation.glass_cost or 0

        # Пересчитываем итоговую стоимость по единой политике (P1-09)
        policy = await load_default_policy(session)
        cost_breakdown = calculate_cost(
            material_cost=material_cost,
            edge_cost=edge_cost,
            facade_cost=facade_cost,
            hardware_cost=hardware_cost,
            glass_cost=glass_cost,
            discount=discount_info,
            **policy.as_cost_kwargs(),
        )

        # Обновляем цены
        calculation.price_after_discount = cost_breakdown.price_after_discount
        calculation.final_price_cash = cost_breakdown.final_cash
        calculation.final_price_noncash = cost_breakdown.final_noncash

        await session.commit()
        break

    discount_info = _extract_discount_info(calculation)
    result = calculate_full_cost(
        modules=calculation.modules or [],
        selected_materials=calculation.selected_materials or {},
        selected_hardware=calculation.selected_hardware or {},
        glass_items=calculation.glass_items or [],
        discount_info=discount_info,
        settings=policy,
    )

    await state.set_state(None)
    await message.answer("✅ Скидка/наценка применена. Возвращаюсь к смете...")

    # Показываем обновлённую смету
    try:
        await show_estimate(message, calculation, result)
    except TelegramBadRequest:
        text = _format_estimate_text(calculation, result)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📄 Скачать КП", callback_data=f"download_kp:{calculation.id}")],
                [InlineKeyboardButton(text="✏️ Скорректировать", callback_data=f"edit_calculation:{calculation.id}")],
                [InlineKeyboardButton(text="💰 Скидка/наценка", callback_data=f"discount_menu:{calculation.id}")],
                [InlineKeyboardButton(text="➕ Ещё вариант", callback_data=f"create_variant:{calculation.id}")],
            ]
        )
        await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("toggle_designer_bonus:"))
async def toggle_designer_bonus(callback: CallbackQuery):
    """Включает/выключает бонус дизайнера."""
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer("Ошибка")
        return

    calculation_id = int(parts[1])

    async for session in get_session():
        calculation = await get_owned_calculation(
            session, calculation_id, callback.from_user.id
        )
        if not calculation:
            await callback.answer("Расчёт не найден")
            return

        # Переключаем бонус
        calculation.designer_bonus_enabled = 1 if not calculation.designer_bonus_enabled else 0

        # Пересчитываем цены
        discount_info = DiscountInfo(
            discount_type=calculation.discount_type,
            discount_value=calculation.discount_value,
            markup_type=calculation.markup_type,
            markup_value=calculation.markup_value,
            designer_bonus_enabled=bool(calculation.designer_bonus_enabled),
            designer_bonus_rate=calculation.designer_bonus_rate or 0.10
        )

        policy = await load_default_policy(session)
        cost_breakdown = calculate_cost(
            material_cost=calculation.material_cost or 0,
            edge_cost=calculation.edge_cost or 0,
            facade_cost=calculation.facade_cost or 0,
            hardware_cost=calculation.hardware_cost or 0,
            glass_cost=calculation.glass_cost or 0,
            lighting_cost=calculation.lighting_cost or 0,
            countertop_cost=calculation.countertop_cost or 0,
            accessories_cost=calculation.accessories_cost or 0,
            subcontractor_cost=calculation.subcontractor_cost or 0,
            fastener_cost=calculation.fastener_cost or 0,
            discount=discount_info,
            **policy.as_cost_kwargs(),
        )

        calculation.price_after_discount = cost_breakdown.price_after_discount
        calculation.final_price_cash = cost_breakdown.final_cash
        calculation.final_price_noncash = cost_breakdown.final_noncash

        await session.commit()
        break

    await callback.answer("Бонус дизайнера переключён")

    # Обновляем меню скидок
    from app.bot.keyboards.inline import get_discount_keyboard
    keyboard = get_discount_keyboard(calculation)
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data.startswith("remove_discount:"))
async def remove_discount(callback: CallbackQuery):
    """Убирает скидку/наценку."""
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer("Ошибка")
        return

    calculation_id = int(parts[1])

    async for session in get_session():
        calculation = await get_owned_calculation(
            session, calculation_id, callback.from_user.id
        )
        if not calculation:
            await callback.answer("Расчёт не найден")
            return

        # Сбрасываем скидки/наценки
        calculation.discount_type = None
        calculation.discount_value = 0
        calculation.markup_type = None
        calculation.markup_value = 0

        # Пересчитываем цены
        discount_info = DiscountInfo(
            designer_bonus_enabled=bool(calculation.designer_bonus_enabled),
            designer_bonus_rate=calculation.designer_bonus_rate or 0.10
        )

        policy = await load_default_policy(session)
        cost_breakdown = calculate_cost(
            material_cost=calculation.material_cost or 0,
            edge_cost=calculation.edge_cost or 0,
            facade_cost=calculation.facade_cost or 0,
            hardware_cost=calculation.hardware_cost or 0,
            glass_cost=calculation.glass_cost or 0,
            lighting_cost=calculation.lighting_cost or 0,
            countertop_cost=calculation.countertop_cost or 0,
            accessories_cost=calculation.accessories_cost or 0,
            subcontractor_cost=calculation.subcontractor_cost or 0,
            fastener_cost=calculation.fastener_cost or 0,
            discount=discount_info,
            **policy.as_cost_kwargs(),
        )

        calculation.price_after_discount = cost_breakdown.price_after_discount
        calculation.final_price_cash = cost_breakdown.final_cash
        calculation.final_price_noncash = cost_breakdown.final_noncash

        await session.commit()
        break

    await callback.answer("Скидка убрана")

    # Обновляем меню скидок
    from app.bot.keyboards.inline import get_discount_keyboard
    keyboard = get_discount_keyboard(calculation)
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data.startswith("back_to_estimate:"))
async def back_to_estimate(callback: CallbackQuery):
    """Возвращается к смете."""
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer("Ошибка")
        return

    calculation_id = int(parts[1])

    async for session in get_session():
        calculation = await get_owned_calculation(
            session, calculation_id, callback.from_user.id
        )
        if not calculation:
            await callback.answer("Расчёт не найден")
            return
        policy = await load_default_policy(session)
        break

    discount_info = _extract_discount_info(calculation)
    result = calculate_full_cost(
        modules=calculation.modules or [],
        selected_materials=calculation.selected_materials or {},
        selected_hardware=calculation.selected_hardware or {},
        glass_items=calculation.glass_items or [],
        discount_info=discount_info,
        settings=policy,
    )

    # Показываем смету
    try:
        await show_estimate(callback.message, calculation, result)
    except TelegramBadRequest:
        text = _format_estimate_text(calculation, result)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📄 Скачать КП", callback_data=f"download_kp:{calculation.id}")],
                [InlineKeyboardButton(text="✏️ Скорректировать", callback_data=f"edit_calculation:{calculation.id}")],
                [InlineKeyboardButton(text="💰 Скидка/наценка", callback_data=f"discount_menu:{calculation.id}")],
                [InlineKeyboardButton(text="➕ Ещё вариант", callback_data=f"create_variant:{calculation.id}")],
            ]
        )
        await callback.message.answer(text, reply_markup=keyboard)

    await callback.answer()