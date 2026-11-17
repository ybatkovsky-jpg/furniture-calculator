"""
Обработчик расчёта стоимости и показа сметы.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.db.session import get_session
from app.domain.validation import PricingError
from app.models.calculation import Calculation
from app.services.access import get_owned_calculation
from app.services.calculation_service import CalculationService, discount_info_from
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
        # Загружаем расчёт из БД, только если он принадлежит этому пользователю
        # (P1-14: чужой id неотличим от несуществующего).
        calculation = await get_owned_calculation(
            session, calculation_id, callback.from_user.id
        )
        if not calculation:
            await callback.message.answer("Ошибка: расчёт не найден в БД.")
            await callback.answer()
            return

        # P1-01: единственная точка пересчёта — CalculationService. Обработчик
        # не собирает прайс/политику вручную и не пишет поля расчёта сам.
        service = CalculationService(session)
        try:
            result = await service.recalculate(calculation, reason="initial")

            # Показываем смету
            await show_estimate(callback.message, calculation, result)

        except PricingError as e:
            await callback.message.answer(f"⛔ {e!s}")
            await callback.answer()
            return
        except Exception as e:  # noqa: BLE001
            await callback.message.answer(f"Ошибка при расчёте: {e!s}")
            await callback.answer()
            return

    await callback.answer()


async def show_estimate(message: Message, calculation: Calculation, result):
    """Показать смету в Telegram."""
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

    if getattr(result, "fastener_cost", 0) > 0:
        lines.append(f"Крепёж: {result.fastener_cost:,.0f} ₽")

    # P0-02: видно, из каких строк собрана себестоимость и где нет цены.
    detail_lines = [line for line in (getattr(result, "lines", None) or []) if line.quantity > 0]
    if detail_lines:
        lines.append("— Строки себестоимости —")
        for line in detail_lines[:14]:
            if line.price_source == "missing":
                lines.append(f"⚠ {line.name}: {line.quantity:g} {line.unit} — цена не задана")
            else:
                lines.append(
                    f"{line.name}: {line.quantity:g} {line.unit} × "
                    f"{line.unit_price:,.0f} = {line.amount:,.0f} ₽"
                )
        if len(detail_lines) > 14:
            lines.append(f"… ещё {len(detail_lines) - 14} строк")

    for warning in getattr(result, "warnings", []) or []:
        lines.append(f"⚠ {warning}")
    if not getattr(result, "can_issue_kp", True):
        lines.append("⛔ КП заблокировано: заполните цены/модели, отмеченные выше")

    # Разделитель
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Детальная разбивка
    breakdown = result.cost_breakdown
    lines.append(f"Себестоимость: {breakdown.cost_price:,.0f} ₽")
    lines.append(f"Изготовление: {breakdown.manufacturing:,.0f} ₽")
    lines.append(f"Монтаж: {breakdown.installation:,.0f} ₽")
    lines.append(f"Замер: {breakdown.measurement:,.0f} ₽")
    lines.append(f"Доставка клиенту: {breakdown.delivery_client:,.0f} ₽ (отдельно)")
    lines.append(f"Доставка внутренняя: {breakdown.delivery_internal:,.0f} ₽")
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
    lines.append(f"Безнал (+13% на мебель): {result.total_noncash:,.0f} ₽")
    lines.append(
        f"УСН 15%: {breakdown.tax_accrual:,.0f} ₽ (НДС 0), "
        f"чистая прибыль: {breakdown.net_profit:,.0f} ₽"
    )
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)


def _extract_discount_info(calculation: Calculation) -> DiscountInfo:
    """Совместимость: скидка из расчёта (переехала в CalculationService)."""
    return discount_info_from(calculation)


