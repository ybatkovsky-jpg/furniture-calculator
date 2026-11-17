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
    _format_estimate_text,
    show_estimate,
)
from app.db.session import get_session
from app.domain.validation import PricingError
from app.services.access import get_owned_calculation
from app.services.calculation_service import CalculationService

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

        # P1-01/P0-01: один полный пересчёт. Раньше здесь вручную собирались
        # 5 компонентов из 10 — скидка занижала себестоимость на 17,8 %.
        # Валидация (P2-06) выполняется в сервисе до записи в БД.
        service = CalculationService(session)
        try:
            if discount_type in ("percent", "fixed"):
                result = await service.apply_discount(
                    calculation,
                    discount_type=discount_type,
                    discount_value=value,
                    reason="discount",
                    user_id=message.from_user.id,
                )
            elif discount_type in ("markup_percent", "markup_fixed"):
                result = await service.apply_discount(
                    calculation,
                    markup_type=discount_type.replace("markup_", ""),
                    markup_value=value,
                    reason="markup",
                    user_id=message.from_user.id,
                )
            else:
                await message.answer("Ошибка: неизвестный тип скидки")
                return
        except PricingError as exc:
            await message.answer(f"❌ {exc}")
            await state.set_state(None)
            return
        break

    await state.set_state(None)
    await message.answer("✅ Скидка/наценка применена. Возвращаюсь к смете...")

    # Показываем обновлённую смету из того же пересчёта, без второго вызова.
    try:
        await show_estimate(message, calculation, result)
    except TelegramBadRequest:
        text = _format_estimate_text(calculation, result)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📄 Скачать КП", callback_data=f"download_kp:{calculation.id}")],
                [InlineKeyboardButton(text="📊 Скачать Excel", callback_data=f"download_excel:{calculation.id}")],
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

        # Переключаем бонус и пересчитываем единым сервисом (P1-01).
        service = CalculationService(session)
        try:
            await service.set_designer_bonus(
                calculation, not bool(calculation.designer_bonus_enabled),
                user_id=callback.from_user.id,
            )
        except PricingError as exc:
            await callback.answer(f"❌ {exc}", show_alert=True)
            return
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

        # Сбрасываем скидку/наценку и пересчитываем единым сервисом (P1-01).
        service = CalculationService(session)
        await service.clear_discount(
            calculation, user_id=callback.from_user.id
        )
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
        result = await CalculationService(session).preview_calculation(calculation)
        break

    # Показываем смету
    try:
        await show_estimate(callback.message, calculation, result)
    except TelegramBadRequest:
        text = _format_estimate_text(calculation, result)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📄 Скачать КП", callback_data=f"download_kp:{calculation.id}")],
                [InlineKeyboardButton(text="📊 Скачать Excel", callback_data=f"download_excel:{calculation.id}")],
                [InlineKeyboardButton(text="✏️ Скорректировать", callback_data=f"edit_calculation:{calculation.id}")],
                [InlineKeyboardButton(text="💰 Скидка/наценка", callback_data=f"discount_menu:{calculation.id}")],
                [InlineKeyboardButton(text="➕ Ещё вариант", callback_data=f"create_variant:{calculation.id}")],
            ]
        )
        await callback.message.answer(text, reply_markup=keyboard)

    await callback.answer()