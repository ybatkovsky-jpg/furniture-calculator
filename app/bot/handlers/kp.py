"""
Обработчик генерации и отправки коммерческих предложений (КП).
"""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile
from sqlalchemy import select

from app.db.session import get_session
from app.models.calculation import Calculation, CommercialOffer
from app.models.project import Project
from app.services.access import get_owned_calculation
from app.services.kp_generator import kp_generator

logger = logging.getLogger(__name__)

router = Router()


def is_legacy_calculation(calculation: Calculation) -> bool:
    """Старый расчёт без снимка цен или с нулевой себестоимостью.

    День 4: такие расчёты не выпускают КП, пока их не пересчитали.
    """
    quote = calculation.quote or {}
    return (
        not quote
        or not calculation.cost_price
        or float(calculation.cost_price) <= 0
    )


@router.callback_query(F.data.startswith("download_kp:"))
async def download_kp(callback: CallbackQuery):
    """
    Обработчик загрузки коммерческого предложения.

    Формат callback_data: "download_kp:{calculation_id}"
    """
    try:
        # Извлечь ID расчёта
        parts = callback.data.split(":")
        if len(parts) != 2:
            await callback.message.answer("❌ Ошибка: некорректный формат данных.")
            await callback.answer()
            return

        calculation_id = int(parts[1])

        async for session in get_session():
            # Загрузить расчёт с проектом (только свой — P1-14)
            calculation = await get_owned_calculation(
                session, calculation_id, callback.from_user.id, with_project=True
            )

            if not calculation:
                await callback.message.answer("❌ Расчёт не найден.")
                await callback.answer()
                return

            # День 4: старые расчёты (без снимка quote или с нулевой
            # себестоимостью) не выпускают КП без пересчёта — иначе клиент
            # получает цену из старых коэффициентов/шаблона.
            quote = calculation.quote or {}
            if is_legacy_calculation(calculation):
                calculation.status = "legacy"
                await session.commit()
                await callback.message.answer(
                    "⛔ Это старый расчёт: нет снимка цен или себестоимость равна 0. "
                    "Пересчитайте смету (выберите материалы заново), затем выпускайте КП."
                )
                await callback.answer()
                return

            # P0-02: КП не выпускается, если в смете есть строки без цены
            # или не выбраны ручки — иначе клиент получает заниженную цену.
            if quote and not quote.get("can_issue_kp", True):
                reasons = quote.get("missing_prices") or quote.get("warnings") or []
                details = "\n".join(f"• {reason}" for reason in reasons[:10])
                await callback.message.answer(
                    "⛔ КП заблокировано: в смете есть позиции без цены.\n"
                    + (details or "• проверьте выбор материалов и прайс")
                )
                await callback.answer()
                return

            # Показать статус генерации
            status_message = await callback.message.answer("📄 Генерирую коммерческое предложение...")

            try:
                # Генерировать PDF
                project_name = calculation.project.project_name or f"Проект {calculation.project.id}"
                pdf_path, kp_number = kp_generator.generate_kp_pdf(
                    calculation,
                    calculation.project,
                    project_name
                )

                # Создать запись в базе данных
                kp_record = kp_generator.create_commercial_offer_record(
                    calculation, kp_number, pdf_path
                )
                session.add(kp_record)
                await session.commit()

                # Отправить PDF как документ
                pdf_file = FSInputFile(pdf_path)
                await callback.message.answer_document(
                    document=pdf_file,
                    caption=f"📄 Коммерческое предложение {kp_number}",
                    filename=f"КП_{kp_number}.pdf"
                )

                # Удалить статусное сообщение
                await status_message.delete()

                # Обновить сообщение с результатом
                success_text = (
                    f"✅ Коммерческое предложение {kp_number} создано!\n\n"
                    f"💰 Итого наличными: {kp_generator._format_currency(kp_record.total_cash)}\n"
                    f"💳 Итого безналичными: {kp_generator._format_currency(kp_record.total_noncash)}"
                )

                # Показать кнопки для дальнейших действий
                from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📤 Отправить клиенту", callback_data=f"send_kp:{kp_record.id}")],
                    [InlineKeyboardButton(text="🔄 Создать вариант", callback_data=f"create_variant:{calculation.id}")],
                    [InlineKeyboardButton(text="⬅️ Назад к смете", callback_data=f"show_estimate:{calculation.id}")],
                ])

                await callback.message.edit_text(
                    success_text,
                    reply_markup=keyboard
                )

            except Exception as e:
                await status_message.edit_text(f"❌ Ошибка при генерации PDF: {e!s}")
                logger.exception("Ошибка генерации PDF")

    except ValueError:
        await callback.message.answer("❌ Ошибка: некорректный ID расчёта.")
    except Exception as e:  # noqa: BLE001
        await callback.message.answer(f"❌ Произошла ошибка: {e!s}")

    await callback.answer()


@router.callback_query(F.data.startswith("send_kp:"))
async def send_kp_to_client(callback: CallbackQuery):
    """
    Отправить КП клиенту (заглушка для будущей реализации).
    """
    kp_id = callback.data.split(":")[1]
    try:
        kp_pk = int(kp_id)
    except ValueError:
        await callback.answer("Некорректный ID КП", show_alert=True)
        return

    # P1-14: чужое КП не должно даже подтверждаться сообщением.
    async for session in get_session():
        stmt = (
            select(CommercialOffer)
            .join(Calculation, CommercialOffer.calculation_id == Calculation.id)
            .join(Project, Calculation.project_id == Project.id)
            .where(
                CommercialOffer.id == kp_pk,
                Project.telegram_user_id == str(callback.from_user.id),
            )
        )
        offer = (await session.execute(stmt)).scalar_one_or_none()
        if offer is None:
            await callback.answer("КП не найдено", show_alert=True)
            return
        break

    await callback.message.answer(
        f"📤 Функция отправки КП клиенту будет реализована позже.\n"
        f"ID КП: {kp_id}"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("create_variant:"))
async def create_calculation_variant(callback: CallbackQuery):
    """
    Создать новый вариант расчёта на основе существующего.
    """
    calculation_id = int(callback.data.split(":")[1])

    async for session in get_session():
        # Загрузить оригинальный расчёт (только свой — P1-14)
        original_calc = await get_owned_calculation(
            session, calculation_id, callback.from_user.id, with_project=True
        )

        if not original_calc:
            await callback.message.answer("❌ Оригинальный расчёт не найден.")
            await callback.answer()
            return

        # Создать новый вариант. Количество считаем запросом, а не ленивой
        # коллекцией project.calculations (она давала MissingGreenlet — P2-19).
        from sqlalchemy import func

        variants_count = await session.scalar(
            select(func.count())
            .select_from(Calculation)
            .where(Calculation.project_id == original_calc.project_id)
        )
        new_variant_name = f"Вариант {(variants_count or 0) + 1}"

        new_calculation = Calculation(
            project_id=original_calc.project_id,
            variant_name=new_variant_name,
            modules=original_calc.modules,
            selected_materials=original_calc.selected_materials,
            selected_hardware=original_calc.selected_hardware,
            glass_items=original_calc.glass_items,
            # P2-04: без zone_type вариант «Кухня» открывался как «Гостиная»,
            # и GOLA/столешница терялись при пересчёте.
            zone_type=original_calc.zone_type,
        )

        session.add(new_calculation)
        await session.flush()

        # P2-19/P1-01: вариант пересчитывается сразу и тем же сервисом, что
        # остальные экраны (раньше — своя копия вызова движка).
        from app.services.calculation_service import CalculationService

        service = CalculationService(session)
        result = await service.recalculate(
            new_calculation, reason="variant", commit=False
        )
        await session.commit()

        await callback.message.answer(
            f"✅ Создан новый вариант: {new_variant_name}\n"
            f"💰 Итого: {result.total_cash:,.0f} ₽\n"
            f"Теперь вы можете скорректировать материалы и сгенерировать новое КП."
        )

    await callback.answer()