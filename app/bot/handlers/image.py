"""
Обработчик фотографий чертежей для распознавания модулей мебели через Gemini Flash.

Пользователь отправляет фото → бот анализирует через Gemini → показывает результат
для подтверждения или коррекции.
"""

import logging
from pathlib import Path
from typing import Optional

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from app.bot.keyboards.inline import recognition_result_keyboard
from app.services.image_analyzer import GeminiImageAnalyzer, RecognitionResult
from app.db.session import get_session
from app.models import Project, Calculation
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = Router()


class ImageRecognitionState(StatesGroup):
    """Состояния процесса распознавания."""
    waiting_for_photo = State()
    showing_result = State()


@router.callback_query(F.data == "send_drawing_photo")
async def request_photo(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал 'Отправить фото чертежа'."""
    await callback.message.answer(
        "📷 Отправьте фотографию чертежа кухонной мебели.\n\n"
        "Убедитесь, что на фото хорошо видны размеры модулей и их расположение."
    )
    await state.set_state(ImageRecognitionState.waiting_for_photo)
    await callback.answer()


@router.message(ImageRecognitionState.waiting_for_photo, F.photo)
async def process_photo(message: Message, state: FSMContext, bot: Bot):
    """Обработать отправленное фото чертежа."""
    try:
        # Получаем информацию о проекте из состояния
        data = await state.get_data()
        project_id = data.get("project_id")
        if not project_id:
            await message.answer("❌ Ошибка: проект не найден. Начните создание проекта заново.")
            await state.clear()
            return

        # Скачиваем фото
        photo = message.photo[-1]  # Берем самое большое разрешение
        file_info = await bot.get_file(photo.file_id)

        # Создаем временную директорию для фото
        temp_dir = Path("temp")
        temp_dir.mkdir(exist_ok=True)

        image_path = temp_dir / f"drawing_{project_id}_{message.message_id}.jpg"

        # Скачиваем файл
        await bot.download_file(file_info.file_path, destination=image_path)

        # Показываем статус
        status_msg = await message.answer("🔍 Распознаю чертёж...")

        # Анализируем через Vision LLM (асинхронно)
        analyzer = GeminiImageAnalyzer()
        result = await analyzer.analyze_drawing(image_path)

        # Логируем результат для отладки
        logger.info(f"Recognition result: confidence={result.confidence}, modules={len(result.modules)}")
        for module in result.modules:
            logger.info(f"  Module: {module.type} {module.width}x{module.depth}x{module.height} qty={module.quantity}")

        # Удаляем временный файл
        image_path.unlink(missing_ok=True)

        # Удаляем статусное сообщение
        await status_msg.delete()

        # Показываем результат
        await show_recognition_result(message, result, state, project_id)

    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        await message.answer("❌ Произошла ошибка при обработке фото. Попробуйте ещё раз.")
        await state.set_state(ImageRecognitionState.waiting_for_photo)


@router.message(ImageRecognitionState.waiting_for_photo)
async def invalid_photo(message: Message):
    """Пользователь отправил не фото."""
    await message.answer(
        "📷 Пожалуйста, отправьте фотографию чертежа.\n\n"
        "Поддерживаются только изображения (фото)."
    )


async def show_recognition_result(
    message: Message,
    result: RecognitionResult,
    state: FSMContext,
    project_id: int
):
    """Показать результат распознавания пользователю."""
    # Форматируем текст с найденными модулями
    text = format_recognition_result(result)

    # Сохраняем результат в состоянии
    await state.update_data(
        recognition_result=result,
        project_id=project_id
    )

    # Если уверенность низкая - переходим на ручной ввод
    if result.confidence == "low":
        await message.answer(
            f"{text}\n\n⚠️ Распознавание неуверенное. Переходим к ручному вводу модулей.",
            reply_markup=None
        )
        # Здесь можно вызвать функцию ручного ввода
        # await start_manual_input(message, state)
        return

    # Показываем результат с кнопками подтверждения
    await message.answer(
        text,
        reply_markup=recognition_result_keyboard()
    )

    await state.set_state(ImageRecognitionState.showing_result)


def format_recognition_result(result: RecognitionResult) -> str:
    """Форматировать результат распознавания для отображения."""
    if not result.modules:
        return "❌ Не удалось распознать модули на чертеже."

    # Группируем модули по типу и размерам
    module_counts = {}

    for module in result.modules:
        # Определяем ключ для группировки
        if module.type == "corner" or module.is_corner:
            key = f"corner_{module.width}x{module.depth}"
            display_type = "corner"
        else:
            key = f"{module.type}_{module.width}x{module.depth}"
            display_type = module.type

        if key not in module_counts:
            module_counts[key] = {
                "display_type": display_type,
                "type": module.type,
                "width": module.width,
                "depth": module.depth,
                "height": module.height,
                "quantity": 0,
                "has_glass": module.has_glass,
                "is_corner": module.type == "corner" or module.is_corner
            }
        module_counts[key]["quantity"] += module.quantity

    # Форматируем текст
    lines = ["🎯 Найдено на чертеже:"]

    type_names = {
        "lower_base": "Нижняя база",
        "upper_base": "Верхняя база",
        "penal": "Пенал",
        "column": "Колонна",
        "tumbler": "Тумба",
        "corner": "Верхняя база угловой модуль"
    }

    for key, info in module_counts.items():
        type_name = type_names.get(info["display_type"], info["display_type"])
        glass_note = " (со стеклом)" if info["has_glass"] else ""

        if info["is_corner"]:
            line = f"• {type_name} {info['width']}×{info['depth']} — {info['quantity']} шт.{glass_note}"
        else:
            line = f"• {type_name} {info['width']}×{info['depth']} — {info['quantity']} шт.{glass_note}"

        lines.append(line)

    if result.notes:
        lines.append(f"\n📝 Примечания: {result.notes}")

    # Уровень уверенности
    confidence_emoji = {
        "high": "🟢",
        "medium": "🟡",
        "low": "🔴"
    }
    conf_emoji = confidence_emoji.get(result.confidence, "❓")
    lines.append(f"\n{conf_emoji} Уверенность распознавания: {result.confidence}")

    return "\n".join(lines)


@router.callback_query(F.data == "confirm_recognition")
async def confirm_recognition(callback: CallbackQuery, state: FSMContext):
    """Пользователь подтвердил распознавание."""
    data = await state.get_data()
    result: RecognitionResult = data.get("recognition_result")
    project_id: int = data.get("project_id")

    if not result or not project_id:
        await callback.answer("❌ Данные распознавания не найдены")
        return

    # Сохраняем распознанные модули в базу данных
    async for session in get_session():
        # Получаем проект
        stmt = select(Project).where(Project.id == project_id)
        result_proj = await session.execute(stmt)
        project = result_proj.scalar_one_or_none()

        if not project:
            await callback.answer("❌ Проект не найден")
            return

        # Создаем или обновляем расчет
        calculation = project.calculation
        if not calculation:
            calculation = Calculation(project_id=project_id)
            session.add(calculation)
            await session.commit()
            await session.refresh(calculation)

        # Конвертируем модули в формат для сохранения
        modules_data = []
        for module in result.modules:
            module_dict = {
                "type": module.type,
                "width": module.width,
                "depth": module.depth,
                "height": module.height,
                "quantity": module.quantity,
                "has_glass": module.has_glass,
                "facades": module.facades,
                "drawers": module.drawers,
                "shelves": module.shelves
            }
            modules_data.append(module_dict)

        # Сохраняем модули
        calculation.modules = modules_data
        await session.commit()
        break

    await callback.message.edit_text(
        "✅ Модули сохранены!\n\n"
        "Теперь выберите материалы для изготовления.",
        reply_markup=None
    )

    # Очищаем состояние
    await state.clear()

    # Здесь можно перейти к выбору материалов
    # await start_material_selection(callback.message, state)


@router.callback_query(F.data == "correct_recognition")
async def correct_recognition(callback: CallbackQuery, state: FSMContext):
    """Пользователь хочет скорректировать распознавание."""
    await callback.message.answer(
        "🔧 Переходим к ручному вводу модулей для коррекции."
    )

    # Очищаем состояние и переходим к ручному вводу
    await state.clear()
    # await start_manual_input(callback.message, state)


@router.callback_query(F.data == "retry_recognition")
async def retry_recognition(callback: CallbackQuery, state: FSMContext):
    """Пользователь хочет отправить другое фото."""
    await callback.message.edit_text(
        "📷 Отправьте новое фото чертежа для повторного распознавания."
    )
    await state.set_state(ImageRecognitionState.waiting_for_photo)