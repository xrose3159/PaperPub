from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BookmarkFolder(Base):
    __tablename__ = "bookmark_folders"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_folder_name"), {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"})

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
