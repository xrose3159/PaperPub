from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

_LongText = Text().with_variant(MEDIUMTEXT, "mysql")


class DailySummary(Base):
    __tablename__ = "daily_summaries"
    __table_args__ = (
        UniqueConstraint("user_id", "summary_date", name="uq_user_summary_date"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True,
    )
    summary_date: Mapped[date] = mapped_column(Date, index=True)
    content: Mapped[str] = mapped_column(_LongText, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()  # noqa: F821
