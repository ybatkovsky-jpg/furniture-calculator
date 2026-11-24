"""Снимки расчётов веб-интерфейса.

Раньше `QUOTES` жил только в памяти процесса: после перезапуска сервера
ссылки на Excel/КП переставали работать. Таблица `web_quotes` хранит тот же
снимок `quote`, что пишется в `Calculation.quote`, но без обязательной связи
с проектом бота (у веба project_id — строковый ID импорта).
"""

from sqlalchemy import JSON, Column, DateTime, Integer, String

from app.db.time import utc_now
from app.models.base import Base


class WebQuote(Base):
    """Снимок веб-расчёта: модули, quote, страница проекта."""

    __tablename__ = "web_quotes"

    id = Column(String(32), primary_key=True)
    project_id = Column(String(16), nullable=True, index=True)
    page_index = Column(Integer, default=0)
    zone_type = Column(String(50), nullable=True)
    modules = Column(JSON, nullable=True)
    quote = Column(JSON, nullable=True)
    validation = Column(JSON, nullable=True)
    image_path = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=utc_now, index=True)

    def __repr__(self) -> str:
        return f"<WebQuote {self.id}>"
