"""Загрузка настроек из переменных окружения и .env."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация приложения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = Field(default="", description="Токен Telegram-бота")

    # Gemini Flash
    gemini_api_key: str = Field(default="", description="Ключ Google Gemini API")

    # База данных
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/furniture.db",
        description="URL подключения к БД",
    )

    # Коэффициенты расчёта
    manufacturing_rate: float = 0.80
    installation_rate: float = 0.55
    design_rate: float = 0.10
    overhead_rate: float = 0.05
    profit_rate: float = 0.30
    tech_director_rate: float = 0.015
    manager_rate: float = 0.0
    designer_rate: float = 0.0
    measurement_fee: float = 2500
    delivery_fee: float = 6500
    delivery_fee_client: float = 10000
    noncash_markup: float = 1.13
    sheet_utilization: float = 0.85

    # Бонус дизайнера
    designer_bonus_rate: float = 0.10


settings = Settings()
