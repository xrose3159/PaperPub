from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DailyRecommendation(Base):
    __tablename__ = "daily_recommendations"
    __table_args__ = (
        UniqueConstraint("user_id", "paper_id", "rec_date", name="uq_user_paper_date"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True,
    )
    paper_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("papers.id", ondelete="CASCADE"), index=True,
    )
    rec_date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()  # noqa: F821
    paper: Mapped["Paper"] = relationship()  # noqa: F821
