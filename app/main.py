"""
Точка входа приложения.

Инициализирует БД и запускает Telegram-бота через polling.
"""

import asyncio
import logging
from app.config import settings
from app.db import init_db
from app.bot.bot import bot, dp, setup_bot_commands
from app.bot.handlers import start_router, price_router, project_router, image_router, manual_input_router, materials_router, calculation_router, discounts_router

# Логирование
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot_debug.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


async def main():
    """Главная функция запуска приложения."""
    
    logger.info("🚀 Запуск мебельного калькулятора...")
    
    # 1. Создаём таблицы в БД
    try:
        await init_db()
        logger.info("✓ Таблицы в БД созданы/актуальны")
    except Exception as e:
        logger.error(f"✗ Ошибка при инициализации БД: {e}")
        raise
    
    # 2. Подключаем routers (обработчики)
    dp.include_router(start_router)
    dp.include_router(price_router)
    dp.include_router(project_router)
    dp.include_router(image_router)
    dp.include_router(manual_input_router)
    dp.include_router(materials_router)
    dp.include_router(calculation_router)
    dp.include_router(discounts_router)
    logger.info("✓ Handlers подключены")
    
    # 3. Устанавливаем команды бота
    try:
        await setup_bot_commands()
    except Exception as e:
        logger.warning(f"⚠ Ошибка при установке команд: {e}")
    
    # 4. Удаляем вебхуки (если были)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✓ Вебхуки очищены")
    
    # 5. Запускаем polling
    logger.info("✓ Бот запущен на polling")
    logger.info(f"📝 Токен бота: {settings.telegram_bot_token[:10]}...")
    logger.info(f"📦 БД: {settings.database_url}")
    
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    except Exception as e:
        logger.error(f"✗ Ошибка при запуске polling: {e}")
        raise
    finally:
        await bot.session.close()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⛔ Приложение остановлено пользователем")
    except Exception as e:
        logger.critical(f"💥 Критическая ошибка: {e}")
        raise
