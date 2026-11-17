"""Навигация: «Мои проекты», «Прайс», «Настройки», возврат к смете (P1-08).

Раньше эти кнопки были нарисованы, но не имели обработчиков: пользователь
попадал в тупик. Здесь — минимальный рабочий сценарий для каждой.
"""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.bot.handlers.calculation import show_estimate
from app.config import settings
from app.db.session import get_session
from app.domain.catalog import build_catalog
from app.models.calculation import Calculation
from app.models.price import PriceItem
from app.models.project import Project
from app.services.access import get_owned_calculation
from app.services.calculation_service import CalculationService
from app.services.pricing_policy import load_default_policy

logger = logging.getLogger(__name__)

router = Router()

PROJECTS_BUTTON = "📋 Мои проекты"
PRICE_BUTTON = "💰 Прайс"
SETTINGS_BUTTON = "⚙️ Настройки"


def _recalc_keyboard(calculation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Скачать КП", callback_data=f"download_kp:{calculation_id}")],
            [InlineKeyboardButton(text="✏️ Скорректировать", callback_data=f"edit_calculation:{calculation_id}")],
            [InlineKeyboardButton(text="💰 Скидка/наценка", callback_data=f"discount_menu:{calculation_id}")],
        ]
    )


@router.message(F.text == PROJECTS_BUTTON)
async def show_projects(message: Message, state: FSMContext):
    """Список последних проектов пользователя."""
    if not message.from_user:
        return

    async for session in get_session():
        result = await session.execute(
            select(Project)
            .options(selectinload(Project.calculations))
            .where(Project.telegram_user_id == str(message.from_user.id))
            .order_by(Project.updated_at.desc(), Project.id.desc())
            .limit(10)
        )
        projects = result.scalars().all()
        break

    if not projects:
        await message.answer(
            "У вас пока нет проектов. Нажмите «📁 Новый проект», чтобы создать первый."
        )
        return

    await state.update_data(project_id=projects[0].id)
    rows = []
    for project in projects:
        if not project.calculations:
            rows.append([
                InlineKeyboardButton(
                    text=f"{project.project_name or project.client_name} — без расчёта",
                    callback_data=f"project_no_calc:{project.id}",
                )
            ])
            continue
        latest = max(project.calculations, key=lambda c: c.id)
        price = latest.final_price_cash or 0
        rows.append([
            InlineKeyboardButton(
                text=f"{project.project_name or project.client_name} — {price:,.0f} ₽",
                callback_data=f"show_estimate:{latest.id}",
            )
        ])

    await message.answer(
        "📋 Ваши проекты (последний расчёт):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("project_no_calc:"))
async def project_without_calculation(callback: CallbackQuery, state: FSMContext):
    project_id = int(callback.data.split(":")[1])
    await state.update_data(project_id=project_id)
    await callback.message.answer(
        "В проекте ещё нет расчёта. Отправьте фото чертежа или PDF-альбом, "
        "либо выберите «✏️ Вручную» после создания проекта."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("show_estimate:"))
async def show_estimate_callback(callback: CallbackQuery):
    """Открыть смету по кнопке (P1-08)."""
    calculation_id = int(callback.data.split(":")[1])

    async for session in get_session():
        calculation = await get_owned_calculation(
            session, calculation_id, callback.from_user.id
        )
        if calculation is None:
            await callback.answer("Расчёт не найден", show_alert=True)
            return
        # P1-11: скидка берётся из самого расчёта, иначе экран показывал
        # цену без скидки. Пересчёт — через единый сервис (P1-01).
        result = await CalculationService(session).preview_calculation(calculation)
        break

    await show_estimate(callback.message, calculation, result)
    await callback.answer()


@router.callback_query(F.data.startswith("edit_calculation:"))
async def edit_calculation(callback: CallbackQuery):
    """«Скорректировать»: показать состав и дать изменить материалы."""
    calculation_id = int(callback.data.split(":")[1])
    async for session in get_session():
        calculation = await get_owned_calculation(
            session, calculation_id, callback.from_user.id
        )
        if calculation is None:
            await callback.answer("Расчёт не найден", show_alert=True)
            return
        modules = calculation.modules or []
        break

    type_names = {
        "lower_base": "нижняя база",
        "upper_base": "верхняя база",
        "penal": "пенал",
        "column": "колонна",
        "tumbler": "тумба",
        "corner": "угловой",
    }
    lines = [f"✏️ Расчёт #{calculation_id}: {len(modules)} модулей"]
    for index, module in enumerate(modules[:15], 1):
        name = type_names.get(module.get("type"), module.get("type", "?"))
        facades = module.get("facades") or {}
        facades_count = facades.get("count", 0) if isinstance(facades, dict) else 0
        lines.append(
            f"{index}. {name} {module.get('width')}×{module.get('depth')}×"
            f"{module.get('height')} — {module.get('quantity', 1)} шт., "
            f"фасадов {facades_count}"
        )
    if len(modules) > 15:
        lines.append(f"… ещё {len(modules) - 15}")

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Выбрать материалы заново", callback_data=f"materials_restart:{calculation_id}")],
            [InlineKeyboardButton(text="💰 Скидка/наценка", callback_data=f"discount_menu:{calculation_id}")],
            [InlineKeyboardButton(text="⬅️ К смете", callback_data=f"show_estimate:{calculation_id}")],
        ]
    )
    await callback.message.answer("\n".join(lines), reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("materials_restart:"))
async def restart_materials(callback: CallbackQuery, state: FSMContext):
    """Перезапуск выбора материалов для существующего расчёта."""
    calculation_id = int(callback.data.split(":")[1])
    async for session in get_session():
        calculation = await get_owned_calculation(
            session, calculation_id, callback.from_user.id
        )
        if calculation is None:
            await callback.answer("Расчёт не найден", show_alert=True)
            return
        break

    from app.bot.handlers.materials import start_material_selection

    await start_material_selection(callback.message, calculation_id, state)
    await callback.answer()


@router.message(F.text == PRICE_BUTTON)
async def show_price(message: Message):
    """«Прайс»: админам — категории, остальным — отказ (P1-14)."""
    if not message.from_user:
        return
    if not settings.is_admin(message.from_user.id):
        await message.answer("⛔ Прайс доступен администратору.")
        return

    async for session in get_session():
        result = await session.execute(
            select(func.count()).select_from(Calculation)
        )
        total = result.scalar() or 0
        break
    await message.answer(
        f"💰 Прайс: управление доступно командами /price_list, /price_search, "
        f"/price_edit, /price_upload.\nВсего расчётов в системе: {total}."
    )


@router.message(F.text == SETTINGS_BUTTON)
async def show_settings(message: Message):
    """«Настройки»: показать действующие коэффициенты и компанию."""
    async for session in get_session():
        policy = await load_default_policy(session)
        result = await session.execute(
            select(PriceItem).where(PriceItem.is_active == 1)
        )
        catalog = build_catalog(result.scalars().all())
        break
    counts = catalog.counts()
    kinds = ", ".join(f"{kind}: {count}" for kind, count in sorted(counts.items()))
    await message.answer(
        "⚙️ Действующие коэффициенты:\n"
        f"• изготовление: {policy.manufacturing_rate:.2f}\n"
        f"• монтаж: {policy.installation_rate:.2f}\n"
        f"• проектировка: {policy.design_rate:.2f} от изготовления\n"
        f"• прочие: {policy.overhead_rate:.2f}\n"
        f"• прибыль: {policy.profit_rate:.2f}\n"
        f"• техдиректор: {policy.tech_director_rate:.3f}\n"
        f"• замер: {policy.measurement_fee:.0f} ₽\n"
        f"• доставка: {policy.delivery_fee:.0f} ₽\n"
        f"• безнал: ×{policy.noncash_markup:.2f}\n\n"
        f"Каталог (активные позиции): {kinds}\n\n"
        f"Компания: {settings.company_name}, {settings.company_phone}"
    )
