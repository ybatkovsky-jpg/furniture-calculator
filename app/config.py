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

    # Администраторы — ID через запятую, которым доступны команды управления прайсом
    # (price_upload, price_edit). Берётся из ADMIN_TELEGRAM_IDS в .env
    admin_telegram_ids: str = Field(default="", description="ID админов через запятую")

    def is_admin(self, user_id: int | str) -> bool:
        """Проверить, является ли пользователь администратором.

        Возвращает True, если список админов пуст (открытый режим,
        как было исторически) ИЛИ user_id есть в ADMIN_TELEGRAM_IDS.
        """
        if not self.admin_telegram_ids.strip():
            return True
        allowed = {s.strip() for s in self.admin_telegram_ids.split(",") if s.strip()}
        return str(user_id) in allowed

    # Vision LLM — Z.ai (основной: GLM-5V-Turbo) / RouterAI.ru (ансамбль: Qwen3-VL)
    zai_api_key: str = Field(default="", description="Ключ Z.ai API (GLM-5V-Turbo, GLM-OCR)")
    openrouter_api_key: str = Field(default="", description="Ключ OpenRouter API (устарел, заменён на RouterAI)")
    routerai_api_key: str = Field(default="", description="Ключ RouterAI.ru API (Qwen3-VL, Gemini Flash) — ансамбль")
    routerai_api_url: str = Field(
        default="https://routerai.ru/api/v1/chat/completions",
        description="URL эндпоинта RouterAI.ru"
    )
    vision_model: str = Field(
        default="glm-5v-turbo",
        description="Модель для распознавания чертежей (glm-4.6v / qwen/qwen3-vl-235b-a22b-instruct)"
    )
    vision_api_url: str = Field(
        default="https://api.z.ai/api/paas/v4/chat/completions",
        description="URL эндпоинта Vision API"
    )

    # Gemini Flash (устаревший, оставлен для совместимости)
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

    # Настройки компании для КП
    company_name: str = "Мебельная Компания"
    company_address: str = "г. Хабаровск, ул. Дикопольцева, 7а"
    company_phone: str = "+7 999 256 3879"
    company_email: str = "pro.mebel25@mail.ru"
    kp_number_prefix: str = "КП-"


settings = Settings()
