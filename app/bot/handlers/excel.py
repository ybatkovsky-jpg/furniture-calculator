"""«📊 Скачать Excel» на экране сметы (P1-14).

Excel собирается из сохранённого снимка `quote` тем же кодом, что CLI
(`fill_template_from_quote`), — второго расчёта нет. Файл удаляется после
отправки.
"""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile

from app.bot.handlers.kp import is_legacy_calculation
from app.db.session import get_session
from app.services.access import get_owned_calculation
from app.services.excel_export import render_quote_excel_async, safe_excel_filename

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data.startswith("download_excel:"))
async def download_excel(callback: CallbackQuery):
    """Сгенерировать и отправить Excel по снимку расчёта."""
    try:
        calculation_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID расчёта", show_alert=True)
        return

    async for session in get_session():
        calculation = await get_owned_calculation(
            session, calculation_id, callback.from_user.id, with_project=True
        )
        if not calculation:
            await callback.answer("Расчёт не найден", show_alert=True)
            return
        if is_legacy_calculation(calculation):
            await callback.answer(
                "Старый расчёт без снимка цен: пересчитайте смету, затем "
                "скачивайте Excel",
                show_alert=True,
            )
            return
        # Копируем данные: сессия закроется до рендера (P0-02: снимок immutable).
        quote = dict(calculation.quote or {})
        project_name = (
            calculation.project.project_name
            if calculation.project is not None
            else None
        )
        break

    status = await callback.message.answer("📊 Собираю Excel из снимка расчёта…")
    path = None
    try:
        path = await render_quote_excel_async(quote, sheet_name=project_name or "Смета")
        await callback.message.answer_document(
            FSInputFile(path),
            filename=safe_excel_filename(calculation_id, project_name),
            caption=f"📊 Смета #{calculation_id} (те же числа, что в чате и КП)",
        )
    except Exception as exc:
        logger.exception("Не удалось собрать Excel по расчёту %s", calculation_id)
        await callback.message.answer(f"❌ Не удалось собрать Excel: {exc!s}")
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
        await status.delete()
    await callback.answer()
