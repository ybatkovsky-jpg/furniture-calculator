"""
Обработчик генерации и отправки коммерческих предложений (КП).
"""

from aiogram import Router, F
from aiogram.types import CallbackQuery, FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.bot.keyboards.inline import get_discount_keyboard
from app.db.session import get_session
from app.models.calculation import Calculation, CommercialOffer
from app.services.kp_generator import kp_generator

router = Router()


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
            # Загрузить расчёт с проектом
            stmt = select(Calculation).options(selectinload(Calculation.project)).where(Calculation.id == calculation_id)
            result = await session.execute(stmt)
            calculation = result.scalar_one_or_none()

            if not calculation:
                await callback.message.answer("❌ Расчёт не найден.")
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
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
                await status_message.edit_text(f"❌ Ошибка при генерации PDF: {str(e)}")
                # Можно добавить логирование ошибки
                import logging
                logging.error(f"PDF generation error: {e}", exc_info=True)

    except ValueError:
        await callback.message.answer("❌ Ошибка: некорректный ID расчёта.")
    except Exception as e:
        await callback.message.answer(f"❌ Произошла ошибка: {str(e)}")

    await callback.answer()


@router.callback_query(F.data.startswith("send_kp:"))
async def send_kp_to_client(callback: CallbackQuery):
    """
    Отправить КП клиенту (заглушка для будущей реализации).
    """
    kp_id = callback.data.split(":")[1]
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
        # Загрузить оригинальный расчёт
        stmt = select(Calculation).options(selectinload(Calculation.project)).where(Calculation.id == calculation_id)
        result = await session.execute(stmt)
        original_calc = result.scalar_one_or_none()

        if not original_calc:
            await callback.message.answer("❌ Оригинальный расчёт не найден.")
            await callback.answer()
            return

        # Создать новый вариант
        new_variant_name = f"Вариант {len(original_calc.project.calculations) + 1}"

        new_calculation = Calculation(
            project_id=original_calc.project_id,
            variant_name=new_variant_name,
            modules=original_calc.modules,
            selected_materials=original_calc.selected_materials,
            selected_hardware=original_calc.selected_hardware,
            glass_items=original_calc.glass_items,
            # Копировать остальные поля...
        )

        session.add(new_calculation)
        await session.commit()

        await callback.message.answer(
            f"✅ Создан новый вариант: {new_variant_name}\n"
            f"Теперь вы можете скорректировать материалы и сгенерировать новое КП."
        )

    await callback.answer()