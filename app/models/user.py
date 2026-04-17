from datetime import datetime

from sqlalchemy import DateTime, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    avatar: Mapped[str | None] = mapped_column(String(256), default=None)
    bio: Mapped[str | None] = mapped_column(Text, default=None)
    interests: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    agents: Mapped[list["Agent"]] = relationship(back_populates="owner")  # noqa: F821

    def __repr__(self) -> str:
        return f"<User {self.username}>"
