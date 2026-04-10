"""
Обработчик скидок/наценок/бонуса дизайнера.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.db.session import get_session
from app.models.calculation import Calculation
from app.services.cost_calc import DiscountInfo, calculate_cost
from app.services.calc_engine import calculate_full_cost
from app.bot.handlers.calculation import _extract_discount_info, show_estimate, _format_estimate_text

router = Router()


class DiscountState(StatesGroup):
    entering_value = State()


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

    if not calculation_id:
        await message.answer("Ошибка: расчёт не найден")
        return

    # Обновляем расчёт в БД
    async for session in get_session():
        calculation = await session.get(Calculation, calculation_id)
        if not calculation:
            await message.answer("Ошибка: расчёт не найден")
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

        # Пересчитываем итоговую стоимость
        cost_breakdown = calculate_cost(
            material_cost=material_cost,
            edge_cost=edge_cost,
            facade_cost=facade_cost,
            hardware_cost=hardware_cost,
            glass_cost=glass_cost,
            discount=discount_info
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
        settings=None
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
                [InlineKeyboardButton(text="➕ Ещё вариант", callback_data=f"new_variant:{calculation.id}")],
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
        calculation = await session.get(Calculation, calculation_id)
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

        cost_breakdown = calculate_cost(
            material_cost=calculation.material_cost or 0,
            edge_cost=calculation.edge_cost or 0,
            facade_cost=calculation.facade_cost or 0,
            hardware_cost=calculation.hardware_cost or 0,
            glass_cost=calculation.glass_cost or 0,
            discount=discount_info
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
        calculation = await session.get(Calculation, calculation_id)
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

        cost_breakdown = calculate_cost(
            material_cost=calculation.material_cost or 0,
            edge_cost=calculation.edge_cost or 0,
            facade_cost=calculation.facade_cost or 0,
            hardware_cost=calculation.hardware_cost or 0,
            glass_cost=calculation.glass_cost or 0,
            discount=discount_info
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
        calculation = await session.get(Calculation, calculation_id)
        if not calculation:
            await callback.answer("Расчёт не найден")
            return
        break

    discount_info = _extract_discount_info(calculation)
    result = calculate_full_cost(
        modules=calculation.modules or [],
        selected_materials=calculation.selected_materials or {},
        selected_hardware=calculation.selected_hardware or {},
        glass_items=calculation.glass_items or [],
        discount_info=discount_info,
        settings=None
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
                [InlineKeyboardButton(text="➕ Ещё вариант", callback_data=f"new_variant:{calculation.id}")],
            ]
        )
        await callback.message.answer(text, reply_markup=keyboard)

    await callback.answer()