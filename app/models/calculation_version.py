"""История снимков расчёта (P1-10).

`Calculation.quote` — последний снимок (для совместимости), `CalculationVersion` —
неизменяемая история: каждая перезапись расчёта создаёт новую версию. Старые
снимки не переписываются, поэтому можно доказать, по какому прайсу и политике
считали.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.models.base import Base


class CalculationVersion(Base):
    """Одна версия снимка расчёта."""

    __tablename__ = "calculation_versions"
    __table_args__ = (
        UniqueConstraint("calculation_id", "version_no", name="uq_calc_version_no"),
    )

    id = Column(Integer, primary_key=True, index=True)
    calculation_id = Column(
        Integer, ForeignKey("calculations.id"), nullable=False, index=True
    )
    calculation = relationship("Calculation", backref="versions")

    version_no = Column(Integer, nullable=False)  # 1, 2, 3… по расчёту
    reason = Column(String(50), nullable=False, default="manual")
    quote = Column(JSON, nullable=False)  # полный снимок v2/v3

    price_list_version = Column(String(64), nullable=True)
    policy_version = Column(String(20), nullable=True)
    engine_version = Column(String(20), nullable=True)

    created_by = Column(Integer, nullable=True)  # telegram id, кто пересчитал
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), index=True)

    def __repr__(self) -> str:
        return (
            f"<CalculationVersion calc={self.calculation_id} "
            f"v{self.version_no} {self.reason}>"
        )
