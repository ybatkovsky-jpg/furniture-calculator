import argparse
import asyncio
from pathlib import Path

from app.db.session import AsyncSessionLocal, init_db
from app.services.price_manager import import_price_from_xlsx


async def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт прайса из XLSX в базу данных.")
    parser.add_argument("xlsx_path", help="Путь к файлу XLSX с прайс-листом")
    args = parser.parse_args()

    file_path = Path(args.xlsx_path)
    if not file_path.exists() or not file_path.suffix.lower() == ".xlsx":
        raise SystemExit("Файл не найден или формат не поддерживается. Укажите корректный .xlsx файл.")

    await init_db()
    async with AsyncSessionLocal() as session:
        result = await import_price_from_xlsx(str(file_path), session, changed_by="script")

    print(f"Импорт завершен. Создано: {result['created']}, обновлено: {result['updated']}, пропущено: {result['skipped']}")


if __name__ == "__main__":
    asyncio.run(main())
