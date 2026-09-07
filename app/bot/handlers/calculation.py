"""
Обработчик расчёта стоимости и показа сметы.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.bot.keyboards.inline import get_discount_keyboard
from app.db.session import get_session
from app.models.calculation import Calculation
from app.models.project import Project
from app.services.calc_engine import calculate_full_cost
from app.services.cost_calc import DiscountInfo

router = Router()


@router.callback_query(F.data.startswith("calculate_cost"))
async def start_calculation(callback: CallbackQuery, state: FSMContext):
    """Запуск расчёта стоимости."""
    parts = callback.data.split(":")
    if len(parts) > 1:
        calculation_id = int(parts[1])
    else:
        data = await state.get_data()
        calculation_id = data.get("calculation_id")

    if not calculation_id:
        await callback.message.answer("Ошибка: расчёт не найден.")
        await callback.answer()
        return

    async for session in get_session():
        # Загружаем расчёт из БД
        calculation = await session.get(Calculation, calculation_id)
        if not calculation:
            await callback.message.answer("Ошибка: расчёт не найден в БД.")
            await callback.answer()
            return

        # Запускаем расчёт
        try:
            result = calculate_full_cost(
                modules=calculation.modules or [],
                selected_materials=calculation.selected_materials or {},
                selected_hardware=calculation.selected_hardware or {},
                glass_items=calculation.glass_items or [],
                discount_info=_extract_discount_info(calculation),
                settings=None  # используем настройки по умолчанию
            )

            # Сохраняем результаты в БД
            calculation.material_cost = result.material_cost
            calculation.edge_cost = result.edge_cost
            calculation.facade_cost = result.facade_cost
            calculation.hardware_cost = result.hardware_cost
            calculation.glass_cost = result.glass_cost
            calculation.total_base_cash = result.cost_breakdown.total_base_cash
            calculation.final_price_cash = result.total_cash
            calculation.final_price_noncash = result.total_noncash

            await session.commit()

            # Показываем смету
            await show_estimate(callback.message, calculation, result)

        except Exception as e:
            await callback.message.answer(f"Ошибка при расчёте: {str(e)}")
            await callback.answer()
            return

    await callback.answer()


async def show_estimate(message: Message, calculation: Calculation, result):
    """Показать смету в Telegram."""
    text = _format_estimate_text(calculation, result)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Скачать КП", callback_data=f"download_kp:{calculation.id}")],
            [InlineKeyboardButton(text="✏️ Скорректировать", callback_data=f"edit_calculation:{calculation.id}")],
            [InlineKeyboardButton(text="💰 Скидка/наценка", callback_data=f"discount_menu:{calculation.id}")],
            [InlineKeyboardButton(text="➕ Ещё вариант", callback_data=f"create_variant:{calculation.id}")],
        ]
    )

    await message.edit_text(text, reply_markup=keyboard)


def _format_estimate_text(calculation: Calculation, result) -> str:
    """Форматирует текст сметы."""
    lines = []

    # Шапка
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📊 СМЕТА РАСЧЁТА")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Материалы
    if result.material_cost > 0:
        lines.append(f"ЛДСП/МДФ: {result.sheets_calc.total_sheets} листов = {result.material_cost:,.0f} ₽")

    if result.edge_cost > 0:
        lines.append(f"Кромка: {result.edge_cost:,.0f} ₽")

    if result.facade_cost > 0:
        lines.append(f"Фасады: {result.facade_cost:,.0f} ₽")

    if getattr(result, "hdf_sheets", 0) > 0:
        lines.append(f"ХДФ (задние стенки): {result.hdf_sheets} листов")

    if getattr(result, "countertop_length_m", 0) > 0:
        lines.append(f"Столешница: {result.countertop_length_m:.1f} м")

    if getattr(result, "gola_horizontal_m", 0) > 0 or getattr(result, "gola_vertical_m", 0) > 0:
        lines.append(f"GOLA: гор. {result.gola_horizontal_m:.1f} м + верт. {result.gola_vertical_m:.1f} м")

    if getattr(result, "led_strip_m", 0) > 0:
        lines.append(f"Подсветка (LED): {result.led_strip_m:.1f} м")

    if result.hardware_cost > 0:
        lines.append(f"Фурнитура: {result.hardware_cost:,.0f} ₽")

    if result.glass_cost > 0:
        lines.append(f"Стекло: {result.glass_cost:,.0f} ₽")

    # Разделитель
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Детальная разбивка
    breakdown = result.cost_breakdown
    lines.append(f"Себестоимость: {breakdown.cost_price:,.0f} ₽")
    lines.append(f"Изготовление: {breakdown.manufacturing:,.0f} ₽")
    lines.append(f"Монтаж: {breakdown.installation:,.0f} ₽")
    lines.append(f"Замер: {breakdown.measurement:,.0f} ₽")
    lines.append(f"Доставка: {breakdown.delivery_internal:,.0f} ₽")
    lines.append(f"Проектировка: {breakdown.design:,.0f} ₽")
    lines.append(f"Прочие: {breakdown.overhead:,.0f} ₽")
    lines.append(f"Прибыль: {breakdown.profit:,.0f} ₽")
    lines.append(f"Тех. директор: {breakdown.tech_director:,.0f} ₽")
    lines.append(f"Менеджер: {breakdown.manager_salary:,.0f} ₽")
    lines.append(f"Дизайнер: {breakdown.designer_salary:,.0f} ₽")

    # Скидки/наценки
    if calculation.discount_type == "percent" and calculation.discount_value > 0:
        discount_text = f"Скидка {calculation.discount_value}%: -{breakdown.discount_amount:,.0f} ₽"
        lines.append(discount_text)
    elif calculation.discount_type == "fixed" and calculation.discount_value > 0:
        discount_text = f"Скидка: -{breakdown.discount_amount:,.0f} ₽"
        lines.append(discount_text)
    elif calculation.markup_type == "percent" and calculation.markup_value > 0:
        markup_text = f"Наценка {calculation.markup_value}%: +{breakdown.discount_amount:,.0f} ₽"
        lines.append(markup_text)
    elif calculation.markup_type == "fixed" and calculation.markup_value > 0:
        markup_text = f"Наценка: +{breakdown.discount_amount:,.0f} ₽"
        lines.append(markup_text)

    # Бонус дизайнера
    if calculation.designer_bonus_enabled and breakdown.designer_bonus_amount > 0:
        lines.append(f"Бонус дизайнера (10%): +{breakdown.designer_bonus_amount:,.0f} ₽")

    # Финальные цены
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Итого наличка: {result.total_cash:,.0f} ₽")
    lines.append(f"Безнал: {result.total_noncash:,.0f} ₽")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)


def _extract_discount_info(calculation: Calculation) -> DiscountInfo:
    """Извлекает информацию о скидках из расчёта."""
    return DiscountInfo(
        discount_type=calculation.discount_type,
        discount_value=calculation.discount_value or 0,
        markup_type=calculation.markup_type,
        markup_value=calculation.markup_value or 0,
        designer_bonus_enabled=bool(calculation.designer_bonus_enabled),
        designer_bonus_rate=calculation.designer_bonus_rate or 0.10
    )


