"""Версии веб-расчёта: история правок состава (этап 2).

`web_quotes` хранит текущий снимок, а `web_quote_versions` — предыдущие
редакции. Версия создаётся при распознавании и при каждом «Пересчитать».
"""

from sqlalchemy import JSON, Column, DateTime, Integer, String

from app.db.time import utc_now
from app.models.base import Base


class WebQuoteVersion(Base):
    """Одна сохранённая редакция веб-расчёта."""

    __tablename__ = "web_quote_versions"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(String(32), nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    modules = Column(JSON, nullable=True)
    quote = Column(JSON, nullable=True)
    validation = Column(JSON, nullable=True)
    reason = Column(String(50), nullable=False, default="web-edit")
    created_by = Column(String(50), nullable=False, default="web")
    created_at = Column(DateTime, default=utc_now, index=True)

    def __repr__(self) -> str:
        return f"<WebQuoteVersion {self.quote_id} v{self.version_no}>"
