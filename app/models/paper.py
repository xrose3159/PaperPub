from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, Text, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

_LongText = Text().with_variant(MEDIUMTEXT, "mysql")


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    arxiv_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    abstract: Mapped[str] = mapped_column(_LongText)
    zh_abstract: Mapped[str | None] = mapped_column(_LongText, nullable=True, default=None)
    authors: Mapped[str] = mapped_column(_LongText)
    arxiv_url: Mapped[str] = mapped_column(String(256))
    pdf_url: Mapped[str] = mapped_column(String(256), nullable=True)
    cover_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True, default=None)
    categories: Mapped[str] = mapped_column(String(256))  # 逗号分隔的 arXiv 分类
    ai_category: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    ai_tags: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    core_contribution: Mapped[str | None] = mapped_column(_LongText, nullable=True, default=None)
    core_contribution_en: Mapped[str | None] = mapped_column(_LongText, nullable=True, default=None)
    meta_review: Mapped[str | None] = mapped_column(_LongText, nullable=True, default=None)
    last_meta_review_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    meta_review_trigger_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    github_url: Mapped[str | None] = mapped_column(String(512), nullable=True, default=None)
    github_stars: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    huggingface_url: Mapped[str | None] = mapped_column(String(512), nullable=True, default=None)
    hf_likes: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    affiliations: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    uploaded_by: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    scores: Mapped[list["Score"]] = relationship(  # noqa: F821
        back_populates="paper", cascade="all, delete-orphan",
    )
    comments: Mapped[list["Comment"]] = relationship(  # noqa: F821
        back_populates="paper", cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Paper {self.arxiv_id}: {self.title[:40]}>"
