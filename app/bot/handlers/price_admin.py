import logging
import tempfile
from pathlib import Path
from urllib.parse import unquote_plus

from aiogram import F, Router
from aiogram.enums import ContentType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import or_, select

from app.bot.states.price_admin import PriceAdminStates
from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models.price import PriceHistory, PriceItem
from app.services.price_manager import import_price_from_xlsx

logger = logging.getLogger(__name__)

router = Router()


def _admin_denied(message_or_callback) -> bool:
    """Единая проверка админа для сообщений и callback-ов (P0-05).

    fail-closed: пустой ADMIN_TELEGRAM_IDS означает «админов нет», поэтому
    без явно настроенного списка не выполняется ни одна админ-команда.
    """
    user = getattr(message_or_callback, "from_user", None)
    return user is None or not settings.is_admin(user.id)


def build_price_category_keyboard(categories: list[str]) -> InlineKeyboardMarkup:
    """Клавиатура категорий прайса по 2 в ряд (aiogram 3.31).

    Callback-данные — индекс категории, а не её текст: кириллица в
    percent-кодировке легко превышает лимит Telegram в 64 байта
    («Алюминиевый фасад» — 123 байта), и кнопка перестаёт нажиматься.
    """
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for index, category in enumerate(categories):
        row.append(
            InlineKeyboardButton(
                text=category,
                callback_data=f"price_category:{index}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parse_price_value(value: str) -> float | None:
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


@router.message(Command("price_upload"))
async def cmd_price_upload(message: Message, state: FSMContext):
    if _admin_denied(message):
        logger.warning(
            "Отказ в /price_upload: user_id=%s",
            message.from_user.id if message.from_user else None,
        )
        await message.answer("⛔ Недостаточно прав для загрузки прайса.")
        return
    await state.set_state(PriceAdminStates.waiting_for_file)
    await message.answer("📤 Отправьте файл XLSX с прайсом.")


@router.message(PriceAdminStates.waiting_for_file, F.content_type == ContentType.DOCUMENT)
async def receive_price_document(message: Message, state: FSMContext):
    # Повторная проверка прав: состояние могло быть выставлено другой учёткой
    # или сохранено до перезапуска бота (P0-05).
    if _admin_denied(message):
        logger.warning(
            "Отказ в загрузке прайса: user_id=%s",
            message.from_user.id if message.from_user else None,
        )
        await state.clear()
        await message.answer("⛔ Недостаточно прав для загрузки прайса.")
        return

    document = message.document
    # P2-10: имя файла — только basename (никаких ../), лимит размера,
    # проверка MIME/расширения. Раньше Path(tmpdir)/file_name позволял
    # запись за пределы каталога, а размер не ограничивался.
    file_name = Path(document.file_name or "price.xlsx").name
    if not file_name.lower().endswith(".xlsx"):
        await message.answer("Ошибка: нужен файл в формате .xlsx. Попробуйте еще раз.")
        return
    if document.file_size and document.file_size > 20 * 1024 * 1024:
        await message.answer("Ошибка: файл больше 20 МБ.")
        return
    if document.mime_type and document.mime_type not in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    ):
        await message.answer("Ошибка: неверный тип файла, нужен XLSX.")
        return

    # Проверяем, что пользователь существует
    if not message.from_user:
        await message.answer("Ошибка: пользователь не найден.")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        target_path = Path(tmpdir) / file_name
        try:
            # aiogram 3.31: у Document нет метода download — файл скачивает бот.
            await message.bot.download(document, destination=target_path)
            async with AsyncSessionLocal() as session:
                result = await import_price_from_xlsx(
                    str(target_path), session, changed_by=str(message.from_user.id)
                )
        except Exception:
            logger.exception(
                "Не удалось импортировать прайс: user_id=%s file=%s",
                message.from_user.id,
                file_name,
            )
            await state.clear()
            await message.answer(
                "❌ Не удалось загрузить или импортировать прайс. "
                "Файл не изменён, попробуйте ещё раз."
            )
            return

    logger.info(
        "Импорт прайса: user_id=%s file=%s created=%s updated=%s skipped=%s rejected=%s deactivated=%s",
        message.from_user.id,
        file_name,
        result["created"],
        result["updated"],
        result["skipped"],
        result["rejected"],
        result["deactivated"],
    )
    await state.clear()
    await message.answer(
        "✅ Импорт завершен. "
        f"Создано: {result['created']}, обновлено: {result['updated']}, "
        f"пропущено: {result['skipped']}, отклонено: {result['rejected']}, "
        f"деактивировано: {result['deactivated']}"
    )


@router.message(PriceAdminStates.waiting_for_file)
async def receive_price_non_document(message: Message):
    await message.answer("Пожалуйста, отправьте файл XLSX. Команда /price_upload снова запускает загрузку.")


@router.message(Command("price_search"))
async def cmd_price_search(message: Message):
    # Закупочные цены — не публичная информация (P1-14).
    if _admin_denied(message):
        logger.warning(
            "Отказ в /price_search: user_id=%s",
            message.from_user.id if message.from_user else None,
        )
        await message.answer("⛔ Недостаточно прав для просмотра прайса.")
        return

    text = message.text or ""
    parts = text.split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""
    if not query:
        await message.answer("Использование: /price_search <запрос>")
        return

    async with AsyncSessionLocal() as session:
        stmt = select(PriceItem).where(
            or_(
                PriceItem.name.ilike(f"%{query}%"),
                PriceItem.category.ilike(f"%{query}%"),
                PriceItem.brand.ilike(f"%{query}%"),
            )
        ).limit(30)
        result = await session.execute(stmt)
        items = result.scalars().all()

    if not items:
        await message.answer("По запросу ничего не найдено.")
        return

    lines = []
    for item in items:
        price_text = f"{item.unit_price:.2f}" if item.unit_price is not None else "нет цены"
        manual = " (ручной ввод)" if item.requires_manual_price else ""
        lines.append(
            f"{item.id}. {item.name} — {price_text} ₽/{item.price_unit or 'шт'}{manual}\n{item.category or 'Без категории'}"
        )

    await message.answer("\n\n".join(lines))


@router.message(Command("price_edit"))
async def cmd_price_edit(message: Message):
    if _admin_denied(message):
        logger.warning(
            "Отказ в /price_edit: user_id=%s",
            message.from_user.id if message.from_user else None,
        )
        await message.answer("⛔ Недостаточно прав для редактирования прайса.")
        return

    text = message.text or ""
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Использование: /price_edit <id> <цена>")
        return

    try:
        item_id = int(parts[1])
    except ValueError:
        await message.answer("Ошибка: id должен быть целым числом.")
        return

    new_price = parse_price_value(parts[2])
    if new_price is None or new_price < 0:
        await message.answer(
            "Ошибка: введите корректную неотрицательную цену, например 1250 или 1250.50."
        )
        return

    async with AsyncSessionLocal() as session:
        item = await session.get(PriceItem, item_id)
        if item is None:
            await message.answer("Позиция с таким ID не найдена.")
            return

        old_price = float(item.unit_price or 0)
        item.unit_price = new_price
        item.requires_manual_price = 0
        item.price_unit = item.price_unit or "шт"
        history = PriceHistory(
            price_item=item,
            old_price=old_price,
            new_price=new_price,
            changed_by=str(message.from_user.id),
            change_reason="Редактирование цены через бот",
        )
        session.add(history)
        await session.commit()

    logger.info(
        "Прайс изменён: user_id=%s item_id=%s old_price=%s new_price=%s",
        message.from_user.id,
        item_id,
        old_price,
        new_price,
    )
    await message.answer(f"✅ Цена для позиции {item.name} обновлена до {new_price:.2f} ₽.")


@router.message(Command("price_list"))
async def cmd_price_list(message: Message):
    if _admin_denied(message):
        logger.warning(
            "Отказ в /price_list: user_id=%s",
            message.from_user.id if message.from_user else None,
        )
        await message.answer("⛔ Недостаточно прав для просмотра прайса.")
        return

    async with AsyncSessionLocal() as session:
        stmt = (
            select(PriceItem.category)
            .distinct()
            .where(PriceItem.is_active == 1)
            .order_by(PriceItem.category)
        )
        result = await session.execute(stmt)
        categories = [row[0] for row in result.fetchall() if row[0]]

    if not categories:
        await message.answer("Список цен пуст.")
        return

    await message.answer(
        "Выберите категорию:", reply_markup=build_price_category_keyboard(categories)
    )


@router.callback_query(F.data.startswith("price_category:"))
async def callback_price_category(callback: CallbackQuery):
    if _admin_denied(callback):
        logger.warning(
            "Отказ в price_category: user_id=%s",
            callback.from_user.id if callback.from_user else None,
        )
        await callback.answer("⛔ Недостаточно прав", show_alert=True)
        return

    payload = callback.data.split(":", 1)[1]
    if payload.isdigit():
        # Новый формат: индекс в отсортированном списке активных категорий.
        async with AsyncSessionLocal() as session:
            stmt = (
                select(PriceItem.category)
                .distinct()
                .where(PriceItem.is_active == 1)
                .order_by(PriceItem.category)
            )
            result = await session.execute(stmt)
            categories = [row[0] for row in result.fetchall() if row[0]]
        index = int(payload)
        if index >= len(categories):
            await callback.answer("Категория не найдена, откройте /price_list заново", show_alert=True)
            return
        category = categories[index]
    else:
        # Старый формат с percent-кодированным названием (кнопки до правки).
        category = unquote_plus(payload)

    async with AsyncSessionLocal() as session:
        stmt = select(PriceItem).where(
            PriceItem.category == category,
            PriceItem.is_active == 1,
        ).order_by(PriceItem.name).limit(40)
        result = await session.execute(stmt)
        items = result.scalars().all()

    if not items:
        await callback.message.answer("В этой категории нет активных позиций.")
        await callback.answer()
        return

    lines = [
        f"{item.id}. {item.name} — {item.unit_price:.2f} ₽/{item.price_unit or 'шт'}"
        if item.unit_price is not None
        else f"{item.id}. {item.name} — нет цены/{item.price_unit or 'шт'}"
        for item in items
    ]
    await callback.message.answer(f"Категория: {category}\n\n" + "\n".join(lines))
    await callback.answer()
