from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Bookmark(Base):
    __tablename__ = "bookmarks"
    __table_args__ = (UniqueConstraint("user_id", "paper_id", name="uq_user_paper"), {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"})

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    paper_id: Mapped[int] = mapped_column(Integer, ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    folder_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("bookmark_folders.id", ondelete="SET NULL"), nullable=True, default=None,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
