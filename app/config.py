"""Загрузка настроек из переменных окружения и .env."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация приложения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,  # Settings(environment="prod") в тестах/коде
    )

    # Окружение: dev | prod. В prod приложение не стартует без ADMIN_TELEGRAM_IDS.
    environment: str = Field(
        default="dev",
        validation_alias=AliasChoices("ENV", "ENVIRONMENT"),
        description="Окружение приложения: dev | prod (env: ENV)",
    )

    # Telegram
    telegram_bot_token: str = Field(default="", description="Токен Telegram-бота")

    # Администраторы — ID через запятую, которым доступны команды управления прайсом
    # (price_upload, price_edit). Берётся из ADMIN_TELEGRAM_IDS в .env.
    # Пустой список = НИКТО не админ (fail-closed, P0-05).
    admin_telegram_ids: str = Field(default="", description="ID админов через запятую")

    # Allowlist пользователей бота (ID через запятую). Пусто — доступ разрешён
    # всем; заполнен — только перечисленным (плюс админам). Используется
    # middleware в app/bot/middlewares/access.py.
    allowed_telegram_ids: str = Field(
        default="", description="ID пользователей, которым разрешён доступ к боту"
    )
    # P2-15: осознанное разрешение «пустой allowlist = доступ всем» в prod.
    # По умолчанию False: в prod пустой список — ошибка конфигурации.
    allow_any_user: bool = Field(
        default=False,
        validation_alias=AliasChoices("ALLOW_ANY_USER"),
        description="Разрешить доступ всем при пустом allowlist в prod",
    )

    @staticmethod
    def _parse_ids(raw: str) -> set[str]:
        return {s.strip() for s in (raw or "").split(",") if s.strip()}

    @property
    def admin_ids(self) -> set[str]:
        """Множество ID администраторов (пусто — админов нет)."""
        return self._parse_ids(self.admin_telegram_ids)

    @property
    def allowed_ids(self) -> set[str]:
        """Множество ID из allowlist (пусто — ограничения нет)."""
        return self._parse_ids(self.allowed_telegram_ids)

    @property
    def is_production(self) -> bool:
        """True для prod/production (без учёта регистра)."""
        return self.environment.strip().lower() in {"prod", "production"}

    def is_admin(self, user_id: int | str) -> bool:
        """Проверить, является ли пользователь администратором.

        P0-05: fail-closed — при пустом ADMIN_TELEGRAM_IDS админом не является
        НИКТО. Историческое «пусто = все админы» было уязвимостью: любой
        пользователь мог заменить прайс и цены всех расчётов.
        """
        return str(user_id).strip() in self.admin_ids

    def is_allowed(self, user_id: int | str) -> bool:
        """Проверить доступ пользователя к боту по allowlist.

        Пустой allowlist означает «ограничения нет»: иначе после обновления
        бот оказался бы недоступен всем, включая собственника, до заполнения
        ALLOWED_TELEGRAM_IDS. Админы допускаются всегда.
        """
        allowed = self.allowed_ids
        if str(user_id).strip() in self.admin_ids:
            return True
        return not allowed or str(user_id).strip() in allowed

    def validate_startup(self) -> None:
        """Проверить критичные настройки перед стартом.

        В production пустой ADMIN_TELEGRAM_IDS — ошибка конфигурации: прайс
        остался бы без единого администратора (fail-closed), и управлять им
        было бы некому. Падаем с понятным сообщением, а не молча работаем.
        """
        if self.is_production and not self.admin_ids:
            raise RuntimeError(
                "ADMIN_TELEGRAM_IDS пуст, а ENV=prod. Заполните список ID "
                "администраторов в .env — иначе управление прайсом недоступно."
            )
        # P2-15/S0-4: пустой allowlist в prod = бот открыт любому, кто знает
        # имя. Требуем явный ALLOW_ANY_USER=true, иначе падаем на старте.
        if self.is_production and not self.allowed_ids and not self.allow_any_user:
            raise RuntimeError(
                "ALLOWED_TELEGRAM_IDS пуст, а ENV=prod. Заполните allowlist "
                "или явно разрешите доступ всем: ALLOW_ANY_USER=true."
            )

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

    # Локальный LLM-сервер (OpenAI-compatible, напр. Qwen3.8 GGUF через llama.cpp)
    local_llm_api_url: str = Field(
        default="",
        description="Base URL локального OpenAI-совместимого сервера "
                    "(напр. http://192.168.1.133:8888/v1). Пусто — выключено."
    )
    local_llm_api_key: str = Field(
        default="", description="API-ключ локального сервера (если требуется)"
    )
    local_vision_model: str = Field(
        default="unsloth/Qwen3.8-27B-GGUF",
        description="Имя модели на локальном сервере"
    )

    # DeepSeek Vision (OpenAI-совместимый API, облако). DeepSeek-V4.1-Flash
    # принимает изображения. Два обязательных условия (проверено на альбоме):
    #   1) response_format={"type":"json_object"} — иначе reasoning съедает весь
    #      бюджет и JSON не доходит (finish_reason=length, content пуст);
    #   2) max_tokens ≈ 32000 — на сложных многоракурсных листах (0011) 16000
    #      не хватает: модель рассуждает до 16000 токенов и обрывается.
    deepseek_api_key: str = Field(default="", description="Ключ DeepSeek API (vision)")
    deepseek_api_url: str = Field(
        default="https://api.deepseek.com/v1/chat/completions",
        description="URL эндпоинта DeepSeek (OpenAI-совместимый)"
    )
    deepseek_vision_model: str = Field(
        default="deepseek-flash",
        description="Vision-модель DeepSeek (deepseek-flash = DeepSeek-V4.1-Flash, "
                    "input: text+image)"
    )

    # Принудительный провайдер распознавания. Пусто — авто: local → deepseek →
    # routerai (первый живой). Значение — это ПЕРВЫЙ провайдер, остальные
    # остаются резервом, чтобы падение облака/локала не обнуляло смету.
    vision_provider: str = Field(
        default="",
        description="local | deepseek | routerai — приоритетный провайдер распознавания",
    )

    # OCR-заземление размерных линий: числа с чертежа читает OCR
    # (GLM-OCR/Tesseract), а не догадка VLM — ground truth для total_width_mm.
    # False — конвейер работает как раньше (только ответ модели).
    ocr_dimensions_enabled: bool = Field(
        default=True,
        description="OCR чисел размерных линий для total_width_mm "
                    "(env: OCR_DIMENSIONS_ENABLED)",
    )

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

    # Налоговый режим (подтверждён: УСН «доходы минус расходы», 15%,
    # Хабаровский край; НДС не применяется). Налог считается от прибыли и
    # НЕ влияет на цену клиенту; нужен для управленческого результата.
    tax_regime: str = "УСН доходы минус расходы"
    tax_region: str = "Хабаровский край"
    tax_rate: float = 0.15
    vat_rate: float = 0.0
    min_tax_rate: float = 0.01

    # Бонус дизайнера
    designer_bonus_rate: float = 0.10

    # Настройки компании для КП
    company_name: str = "Мебельная Компания"
    company_address: str = "г. Хабаровск, ул. Дикопольцева, 7а"
    company_phone: str = "+7 999 256 3879"
    company_email: str = "pro.mebel25@mail.ru"
    kp_number_prefix: str = "КП-"


def mask_database_url(url: str) -> str:
    """URL БД без пароля для логов (P2-15)."""
    try:
        from sqlalchemy.engine import make_url

        return make_url(str(url or "")).render_as_string(hide_password=True)
    except Exception:  # noqa: BLE001 — маскировка не должна ронять старт
        return str(url or "")


settings = Settings()
