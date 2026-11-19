import argparse
import asyncio
import sys
from pathlib import Path

# Add the parent directory to sys.path so we can import app
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.session import AsyncSessionLocal, init_db
from app.services.price_manager import import_price_from_xlsx


async def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт прайса из XLSX в базу данных.")
    parser.add_argument("xlsx_path", help="Путь к файлу XLSX с прайс-листом")
    parser.add_argument(
        "--sheet",
        action="append",
        dest="sheets",
        help="Импортировать только этот лист (можно повторять). "
        "Без флага — все листы.",
    )
    parser.add_argument(
        "--update-only",
        action="store_true",
        help="Только обновлять цены существующих позиций: не создавать новые "
        "и не деактивировать отсутствующие (файл — частичный прайс или расчёт).",
    )
    args = parser.parse_args()

    file_path = Path(args.xlsx_path)
    if not file_path.exists() or not file_path.suffix.lower() == ".xlsx":
        raise SystemExit("Файл не найден или формат не поддерживается. Укажите корректный .xlsx файл.")

    await init_db()
    async with AsyncSessionLocal() as session:
        result = await import_price_from_xlsx(
            str(file_path),
            session,
            changed_by="script",
            sheet_names=args.sheets,
            update_only=args.update_only,
        )

    print(
        f"Импорт завершен. Создано: {result['created']}, обновлено: {result['updated']}, "
        f"пропущено: {result['skipped']}, деактивировано: {result['deactivated']}, "
        f"нет в прайсе: {result.get('unknown', 0)}"
    )


if __name__ == "__main__":
    asyncio.run(main())
