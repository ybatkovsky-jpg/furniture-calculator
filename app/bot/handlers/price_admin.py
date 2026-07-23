import tempfile
from pathlib import Path
from urllib.parse import quote_plus, unquote_plus

from aiogram import F, Router
from aiogram.enums import ContentType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import or_, select

from app.bot.states.price_admin import PriceAdminStates
from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models.price import PriceHistory, PriceItem
from app.services.price_manager import import_price_from_xlsx

router = Router()


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
    if not settings.is_admin(message.from_user.id if message.from_user else 0):
        await message.answer("⛔ Недостаточно прав для загрузки прайса.")
        return
    await state.set_state(PriceAdminStates.waiting_for_file)
    await message.answer("📤 Отправьте файл XLSX с прайсом.")


@router.message(PriceAdminStates.waiting_for_file, F.content_type == ContentType.DOCUMENT)
async def receive_price_document(message: Message, state: FSMContext):
    document = message.document
    file_name = document.file_name or "price.xlsx"
    if not file_name.lower().endswith(".xlsx"):
        await message.answer("Ошибка: нужен файл в формате .xlsx. Попробуйте еще раз.")
        return

    # Проверяем, что пользователь существует
    if not message.from_user:
        await message.answer("Ошибка: пользователь не найден.")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        target_path = Path(tmpdir) / file_name
        await document.download(destination_file=str(target_path))
        async with AsyncSessionLocal() as session:
            result = await import_price_from_xlsx(str(target_path), session, changed_by=str(message.from_user.id))

    await state.clear()
    await message.answer(
        f"✅ Импорт завершен. Создано: {result['created']}, обновлено: {result['updated']}, пропущено: {result['skipped']}"
    )


@router.message(PriceAdminStates.waiting_for_file)
async def receive_price_non_document(message: Message):
    await message.answer("Пожалуйста, отправьте файл XLSX. Команда /price_upload снова запускает загрузку.")


@router.message(Command("price_search"))
async def cmd_price_search(message: Message):
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
    # Проверяем, что пользователь существует
    if not message.from_user:
        await message.answer("Ошибка: пользователь не найден.")
        return
    if not settings.is_admin(message.from_user.id):
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
    if new_price is None:
        await message.answer("Ошибка: введите корректную цену, например 1250 или 1250.50.")
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

    await message.answer(f"✅ Цена для позиции {item.name} обновлена до {new_price:.2f} ₽.")


@router.message(Command("price_list"))
async def cmd_price_list(message: Message):
    async with AsyncSessionLocal() as session:
        stmt = select(PriceItem.category).distinct().where(PriceItem.is_active == 1)
        result = await session.execute(stmt)
        categories = [row[0] for row in result.fetchall() if row[0]]

    if not categories:
        await message.answer("Список цен пуст.")
        return

    keyboard = InlineKeyboardMarkup(row_width=2)
    for category in categories:
        keyboard.add(
            InlineKeyboardButton(
                text=category,
                callback_data=f"price_category:{quote_plus(category)}",
            )
        )

    await message.answer("Выберите категорию:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("price_category:"))
async def callback_price_category(callback):
    category = unquote_plus(callback.data.split(":", 1)[1])
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
