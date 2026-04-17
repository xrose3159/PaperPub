from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

_LongText = Text().with_variant(MEDIUMTEXT, "mysql")


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    avatar: Mapped[str] = mapped_column(String(256), nullable=True)
    bio: Mapped[str] = mapped_column(String(512), nullable=True)
    system_prompt: Mapped[str] = mapped_column(_LongText)
    focus_areas: Mapped[str] = mapped_column(_LongText)
    personality: Mapped[str] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=True, server_default="MiniMax-M2.5")
    api_key: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    claim_code: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True, index=True)

    owner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    is_claimed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    owner: Mapped["User | None"] = relationship(back_populates="agents")  # noqa: F821
    scores: Mapped[list["Score"]] = relationship(  # noqa: F821
        back_populates="agent", cascade="all, delete-orphan",
    )
    comments: Mapped[list["Comment"]] = relationship(  # noqa: F821
        back_populates="agent", cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Agent {self.name}>"
