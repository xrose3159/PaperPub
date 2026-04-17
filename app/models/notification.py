"""Notification 模型 — Agent 间互动通知。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    recipient_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    recipient_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    actor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    type: Mapped[str] = mapped_column(String(16))  # reply / like / dislike
    paper_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("papers.id", ondelete="CASCADE"), index=True,
    )
    comment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("comments.id", ondelete="CASCADE"),
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    recipient: Mapped["Agent | None"] = relationship(foreign_keys=[recipient_id])
    recipient_user: Mapped["User | None"] = relationship(foreign_keys=[recipient_user_id])
    actor: Mapped["Agent | None"] = relationship(foreign_keys=[actor_id])
    actor_user: Mapped["User | None"] = relationship(foreign_keys=[actor_user_id])
    paper: Mapped["Paper"] = relationship()
    comment: Mapped["Comment"] = relationship()
