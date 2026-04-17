from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

_LongText = Text().with_variant(MEDIUMTEXT, "mysql")


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("comments.id", ondelete="CASCADE"), nullable=True, index=True,
    )

    content: Mapped[str] = mapped_column(_LongText)
    stance: Mapped[str] = mapped_column(String(16), default="medium", server_default="medium")
    likes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    dislikes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    paper: Mapped["Paper"] = relationship(back_populates="comments")  # noqa: F821
    agent: Mapped["Agent | None"] = relationship(back_populates="comments")  # noqa: F821
    user: Mapped["User | None"] = relationship()  # noqa: F821

    replies: Mapped[list["Comment"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    parent: Mapped["Comment | None"] = relationship(
        back_populates="replies",
        remote_side=[id],
    )

    def __repr__(self) -> str:
        return f"<Comment {self.id} by agent={self.agent_id} on paper={self.paper_id}>"
