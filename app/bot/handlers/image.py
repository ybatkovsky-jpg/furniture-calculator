"""
Обработчик фотографий чертежей для распознавания модулей мебели через Gemini Flash.

Пользователь отправляет фото → бот анализирует через Gemini → показывает результат
для подтверждения или коррекции.
"""

import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.keyboards.inline import (
    album_preview_keyboard,
    recognition_result_keyboard,
)
from app.db.session import get_session
from app.domain.recognition_validation import validate_modules
from app.models import Calculation
from app.services.access import get_owned_project
from app.services.image_analyzer import GeminiImageAnalyzer, RecognitionResult

logger = logging.getLogger(__name__)

router = Router()

# P2-20: простое ограничение частоты тяжёлых vision/PDF-запросов.
_LAST_HEAVY_REQUEST: dict[int, float] = {}
HEAVY_REQUEST_INTERVAL_SEC = 5.0


def _is_rate_limited(user_id: int, now: float | None = None) -> bool:
    import time

    current = time.monotonic() if now is None else now
    last = _LAST_HEAVY_REQUEST.get(user_id, 0.0)
    if current - last < HEAVY_REQUEST_INTERVAL_SEC:
        return True
    _LAST_HEAVY_REQUEST[user_id] = current
    return False


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
    if message.from_user and _is_rate_limited(message.from_user.id):
        await message.answer("⏳ Подождите пару секунд перед следующим распознаванием.")
        return
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

    except Exception as e:  # noqa: BLE001
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


@router.message(F.document)
async def process_pdf_album(message: Message, state: FSMContext, bot: Bot):
    """P1-06: приём PDF-альбома чертежей в боте.

    PDF → FullPipeline (OCR → рендер → Vision) → модули → расчёт проекта.
    Раньше бот принимал только фото, хотя README обещает альбом.
    """
    document = message.document
    if document is None or not message.from_user:
        return
    if _is_rate_limited(message.from_user.id):
        await message.answer("⏳ Подождите пару секунд перед следующей загрузкой.")
        return

    file_name = document.file_name or "album.pdf"
    if not file_name.lower().endswith(".pdf"):
        await message.answer(
            "Поддерживается PDF-альбом чертежей или фото листа. "
            "Отправьте файл .pdf либо фотографию."
        )
        return

    max_bytes = 50 * 1024 * 1024
    if document.file_size and document.file_size > max_bytes:
        await message.answer("❌ PDF больше 50 МБ — разделите альбом на части.")
        return

    data = await state.get_data()
    project_id = data.get("project_id")
    if not project_id:
        await message.answer(
            "Сначала создайте проект: «📁 Новый проект», затем отправьте PDF."
        )
        return

    async for session in get_session():
        project = await get_owned_project(session, project_id, message.from_user.id)
        if project is None:
            await message.answer("❌ Проект не найден.")
            return
        break

    temp_dir = Path("temp")
    temp_dir.mkdir(exist_ok=True)
    pdf_path = temp_dir / f"album_{project_id}_{message.message_id}.pdf"
    status = await message.answer("📄 Принял PDF. Распознаю альбом — это может занять несколько минут…")

    from app.services.full_pipeline import FullPipeline

    pipeline = FullPipeline()
    try:
        await bot.download(document, destination=pdf_path)
        result = await pipeline.process(pdf_path)
    except Exception as exc:
        logger.exception("PDF pipeline failed")
        await status.edit_text(f"❌ Не удалось обработать PDF: {exc}")
        return
    finally:
        await pipeline.close()
        pdf_path.unlink(missing_ok=True)

    if not result.rooms:
        await status.edit_text(
            "❌ В PDF не найдено помещений с мебелью. "
            "Проверьте, что это альбом чертежей, а не скан текста."
        )
        return

    # День 3: сначала показываем распознанное и не сохраняем в расчёт до
    # подтверждения оператора. Комнаты храним отдельно, чтобы не смешать зоны.
    album_rooms = [
        {
            "name": room.room_name,
            "zone_type": room.zone_type,
            "confidence": room.confidence,
            "quality_score": room.quality_score,
            "room_id": room.room_id,
            "view_type": room.view_type,
            "view_group": room.view_group,
            "is_duplicate_view": room.is_duplicate_view,
            "duplicate_of_page": room.duplicate_of_page,
            "projection_warnings": room.projection_warnings,
            "modules": [_module_to_dict(module) for module in room.modules],
        }
        for room in result.rooms
        if room.modules
    ]
    if not album_rooms:
        await status.edit_text("❌ Модули не распознаны. Проверьте качество скана.")
        return

    await state.update_data(
        album_rooms=album_rooms,
        project_id=project_id,
        album_confidence=result.confidence,
    )
    await status.edit_text(
        build_album_preview(album_rooms),
        reply_markup=album_preview_keyboard(),
    )


def _module_to_dict(module) -> dict:
    """Модуль распознавания → JSON для Calculation (без потери полей)."""
    return {
        "type": module.type,
        "width": module.width,
        "depth": module.depth,
        "height": module.height,
        "quantity": module.quantity,
        "has_glass": module.has_glass,
        "facades": module.facades,
        "drawers": module.drawers,
        "shelves": module.shelves,
        "is_corner": module.is_corner,
        "notes": module.notes,
    }


def _count(value) -> int:
    """Количество из facades/drawers: dict {"count": N}, int или None."""
    if isinstance(value, dict):
        try:
            return int(value.get("count") or 0)
        except (TypeError, ValueError):
            return 0
    if isinstance(value, int):
        return value
    return 0


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

    # День 3: низкая уверенность — не тупик. Оператор видит то же самое и
    # может подтвердить, исправить или отправить новое фото.
    if result.confidence == "low":
        text += (
            "\n\n⚠️ Уверенность низкая: проверьте размеры, двери и ящики. "
            "Лучше нажать «Скорректировать» и ввести модули вручную."
        )

    text += format_validation_questions(result)

    await message.answer(text, reply_markup=recognition_result_keyboard())
    await state.set_state(ImageRecognitionState.showing_result)


def format_validation_questions(result: RecognitionResult) -> str:
    """Блокирующие вопросы этапа 2: что менеджер должен уточнить."""
    validation = validate_modules(
        result.modules,
        confidence=result.confidence,
    )
    if not validation.blocking and not validation.questions:
        return ""
    lines = ["\n\n❓ Проверьте перед расчётом:"]
    lines.extend(f"• {question}" for question in validation.questions[:5])
    if validation.blocking:
        lines.append("⛔ Без ответа на эти вопросы расчёт будет заблокирован.")
    return "\n".join(lines)


def format_recognition_result(result: RecognitionResult) -> str:
    """Форматировать результат распознавания для отображения.

    День 3: показываем не только Ш×Г, но и высоту, число корпусов, дверей,
    ящиков и стекло — иначе оператор не может проверить то, что попадёт в смету.
    """
    if not result.modules:
        return "❌ Не удалось распознать модули на чертеже."

    type_names = {
        "lower_base": "Нижняя база",
        "upper_base": "Верхняя база",
        "penal": "Пенал",
        "column": "Колонна",
        "tumbler": "Тумба",
        "cabinet": "Тумба",
        "corner": "Угловой модуль",
    }

    # Группируем одинаковые модули (тип + габарит + двери/ящики/стекло).
    module_counts: dict[tuple, dict] = {}
    for module in result.modules:
        is_corner = module.type == "corner" or module.is_corner
        display_type = "corner" if is_corner else module.type
        key = (
            display_type,
            module.width,
            module.depth,
            module.height,
            _count(module.facades),
            _count(module.drawers),
            bool(module.has_glass),
        )
        if key not in module_counts:
            module_counts[key] = {
                "type": display_type,
                "width": module.width,
                "depth": module.depth,
                "height": module.height,
                "quantity": 0,
                "doors": 0,
                "drawers": 0,
                "has_glass": module.has_glass,
                "notes": [],
            }
        info = module_counts[key]
        info["quantity"] += module.quantity
        info["doors"] += _count(module.facades) * module.quantity
        info["drawers"] += _count(module.drawers) * module.quantity
        if module.notes:
            info["notes"].append(module.notes)

    lines = ["🎯 Найдено на чертеже:"]
    if result.zone_type:
        lines.append(f"🏠 Зона: {result.zone_type}")
    lines.append("")

    for info in module_counts.values():
        type_name = type_names.get(info["type"], info["type"])
        parts = [
            f"• {type_name} {info['width']}×{info['depth']}×{info['height']} мм",
            f"{info['quantity']} корп.",
        ]
        if info["doors"]:
            parts.append(f"{info['doors']} двер.")
        if info["drawers"]:
            parts.append(f"{info['drawers']} ящ.")
        if info["has_glass"]:
            parts.append("стекло")
        line = " — ".join([parts[0], ", ".join(parts[1:])])
        if info["notes"]:
            line += f" (⚠ {info['notes'][0]})"
        lines.append(line)

    if result.notes:
        lines.append(f"\n📝 Примечания: {result.notes}")

    confidence_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}
    conf_emoji = confidence_emoji.get(result.confidence, "❓")
    lines.append(f"\n{conf_emoji} Уверенность распознавания: {result.confidence}")

    return "\n".join(lines)


def build_album_preview(rooms: list[dict]) -> str:
    """Текст предпросмотра PDF-альбома: комнаты и модули до сохранения."""
    total_modules = sum(
        sum(int(module.get("quantity") or 0) for module in room.get("modules", []))
        for room in rooms
        if not room.get("is_duplicate_view")
    )
    lines = [
        f"📐 Распознано помещений: {len(rooms)}, модулей: {total_modules}.",
        (
            "Проверьте комнаты и модули. Только после «Подтвердить» они попадут "
            "в отдельные расчёты (по комнате на расчёт)."
        ),
    ]
    type_names = {
        "lower_base": "Нижняя база",
        "upper_base": "Верхняя база",
        "penal": "Пенал",
        "column": "Колонна",
        "tumbler": "Тумба",
        "cabinet": "Тумба",
        "corner": "Угловой модуль",
    }
    for room in rooms:
        modules = room.get("modules", [])
        duplicate = room.get("is_duplicate_view")
        lines.append("")
        suffix = ""
        if duplicate:
            suffix = f" — дубль проекции стр. {room.get('duplicate_of_page')}, в расчёт не войдёт"
        lines.append(
            f"🏠 {room.get('name') or 'Без названия'} — "
            f"{len(modules)} поз., {sum(int(m.get('quantity') or 0) for m in modules)} корп.{suffix}"
        )
        for warning in room.get("projection_warnings") or []:
            lines.append(f"  ⚠ {warning}")
        for module in modules:
            type_name = type_names.get(module.get("type"), module.get("type"))
            quantity = int(module.get("quantity") or 0)
            parts = [
                (
                    f"  • {type_name} "
                    f"{module.get('width')}×{module.get('depth')}×{module.get('height')} мм"
                ),
                f"{module.get('quantity', 1)} корп.",
            ]
            doors = _count(module.get("facades")) * quantity
            drawers = _count(module.get("drawers")) * quantity
            if doors:
                parts.append(f"{doors} двер.")
            if drawers:
                parts.append(f"{drawers} ящ.")
            if module.get("has_glass"):
                parts.append("стекло")
            lines.append(" — ".join([parts[0], ", ".join(parts[1:])]))
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
        # Получаем проект, только если он принадлежит пользователю (P1-14)
        project = await get_owned_project(
            session, project_id, callback.from_user.id
        )

        if not project:
            await callback.answer("❌ Проект не найден")
            return

        # Создаем или обновляем расчёт. Раньше здесь было project.calculation:
        # ленивая загрузка relationship в async-сессии падала MissingGreenlet
        # (P1-05). Берём последний расчёт явным запросом.
        from sqlalchemy import desc

        calc_result = await session.execute(
            select(Calculation)
            .where(Calculation.project_id == project_id)
            .order_by(desc(Calculation.created_at), desc(Calculation.id))
            .limit(1)
        )
        calculation = calc_result.scalar_one_or_none()
        if not calculation:
            calculation = Calculation(project_id=project_id)
            session.add(calculation)
            await session.commit()
            await session.refresh(calculation)

        # Конвертируем модули в формат для сохранения
        modules_data = [_module_to_dict(module) for module in result.modules]

        # Сохраняем модули и тип помещения (P1-20): раньше zone_type терялся,
        # и расчёт угадывал «Кухня» по наличию верхних баз.
        calculation.modules = modules_data
        if result.zone_type:
            calculation.zone_type = result.zone_type
        # P1-13: низкая уверенность сохраняется и блокирует КП до проверки.
        calculation.recognition_confidence = result.confidence
        await session.commit()
        break

    await callback.message.edit_text(
        "✅ Модули сохранены!\n\n"
        "Теперь выберите материалы для изготовления.",
        reply_markup=None
    )

    # P1-05: не тупик — сразу переходим к выбору материалов (раньше обещали,
    # но не запускали). Состояние не очищаем: в нём calculation_id.
    from app.bot.handlers.materials import start_material_selection

    await start_material_selection(callback.message, calculation.id, state)


async def _create_room_calculations(
    project_id: int, rooms: list[dict], user_id: int
) -> list[int]:
    """Создать по одному расчёту на комнату (зоны не смешиваются)."""
    calculation_ids: list[int] = []
    async for session in get_session():
        project = await get_owned_project(session, project_id, user_id)
        if project is None:
            return []
        for room in rooms:
            if room.get("is_duplicate_view"):
                continue
            calculation = Calculation(
                project_id=project_id,
                modules=list(room.get("modules") or []),
                zone_type=room.get("zone_type"),
                recognition_confidence=room.get("confidence"),
                quality_score=room.get("quality_score"),
            )
            session.add(calculation)
            await session.flush()
            calculation_ids.append(calculation.id)
        await session.commit()
        break
    return calculation_ids


async def _start_manual_input_for_calculation(
    message: Message, state: FSMContext, calculation_id: int
) -> None:
    """Запустить ручной ввод в существующий расчёт (без очистки состояния)."""
    from app.bot.keyboards.inline import get_module_type_keyboard
    from app.bot.states.manual_input import ManualInputStates

    await state.update_data(calculation_id=calculation_id, current_modules=[])
    await state.set_state(ManualInputStates.waiting_for_module_type)
    await message.answer(
        "Выберите тип модуля:",
        reply_markup=get_module_type_keyboard(),
    )


@router.callback_query(F.data == "correct_recognition")
async def correct_recognition(callback: CallbackQuery, state: FSMContext):
    """Пользователь хочет скорректировать распознавание.

    День 3: не очищаем состояние «в никуда». Распознанные модули сохраняем в
    черновой расчёт, затем запускаем ручной ввод в этот же расчёт.
    """
    data = await state.get_data()
    result: RecognitionResult | None = data.get("recognition_result")
    project_id = data.get("project_id")
    if not result or not project_id or not callback.from_user:
        await callback.answer("❌ Данные распознавания не найдены", show_alert=True)
        return

    calculations = await _create_room_calculations(
        project_id,
        [{"name": "Правка", "zone_type": result.zone_type,
          "modules": [_module_to_dict(module) for module in result.modules]}],
        callback.from_user.id,
    )
    if not calculations:
        await callback.answer("❌ Проект не найден", show_alert=True)
        return

    await callback.message.answer(
        "✏️ Распознанные модули сохранены в черновик. "
        "Введите модули заново (или добавьте недостающие) — текущий черновик "
        "перезапишется после выбора материалов."
    )
    await _start_manual_input_for_calculation(
        callback.message, state, calculations[0]
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_album")
async def confirm_album(callback: CallbackQuery, state: FSMContext):
    """Подтверждение PDF-альбома: по одному расчёту на комнату."""
    data = await state.get_data()
    rooms: list[dict] = data.get("album_rooms") or []
    project_id = data.get("project_id")
    if not rooms or not project_id or not callback.from_user:
        await callback.answer("❌ Данные альбома не найдены", show_alert=True)
        return

    calculation_ids = await _create_room_calculations(
        project_id, rooms, callback.from_user.id
    )
    if not calculation_ids:
        await callback.answer("❌ Проект не найден", show_alert=True)
        return

    await state.update_data(
        calculation_id=calculation_ids[0],
        album_calc_ids=calculation_ids[1:],
    )
    await callback.message.edit_text(
        f"✅ Сохранено расчётов по комнатам: {len(calculation_ids)}. "
        "Зоны не смешаны.\n"
        f"Начинаем с первой комнаты: {rooms[0].get('name') or 'без названия'}."
    )
    await callback.answer()

    from app.bot.handlers.materials import start_material_selection

    await start_material_selection(callback.message, calculation_ids[0], state)


@router.callback_query(F.data == "correct_album")
async def correct_album(callback: CallbackQuery, state: FSMContext):
    """Исправление альбома: сохраняем комнаты как черновики и вводим вручную."""
    data = await state.get_data()
    rooms: list[dict] = data.get("album_rooms") or []
    project_id = data.get("project_id")
    if not rooms or not project_id or not callback.from_user:
        await callback.answer("❌ Данные альбома не найдены", show_alert=True)
        return

    calculation_ids = await _create_room_calculations(
        project_id, rooms, callback.from_user.id
    )
    if not calculation_ids:
        await callback.answer("❌ Проект не найден", show_alert=True)
        return

    await callback.message.answer(
        f"✏️ Распознанные комнаты сохранены черновиками ({len(calculation_ids)} шт.). "
        "Введите первую комнату вручную; остальные черновики останутся в проекте."
    )
    await _start_manual_input_for_calculation(
        callback.message, state, calculation_ids[0]
    )
    await callback.answer()


@router.callback_query(F.data == "retry_recognition")
async def retry_recognition(callback: CallbackQuery, state: FSMContext):
    """Пользователь хочет отправить другое фото."""
    await callback.message.edit_text(
        "📷 Отправьте новое фото чертежа для повторного распознавания."
    )
    await state.set_state(ImageRecognitionState.waiting_for_photo)