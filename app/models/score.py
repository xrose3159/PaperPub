from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Text, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

_LongText = Text().with_variant(MEDIUMTEXT, "mysql")


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)

    novelty: Mapped[float] = mapped_column(Float, default=0.0)         # 创新性
    rigor: Mapped[float] = mapped_column(Float, default=0.0)           # 数学严谨性
    applicability: Mapped[float] = mapped_column(Float, default=0.0)   # 应用价值
    clarity: Mapped[float] = mapped_column(Float, default=0.0)         # 写作清晰度
    significance: Mapped[float] = mapped_column(Float, default=0.0)    # 研究重要性
    reproducibility: Mapped[float] = mapped_column(Float, default=0.0) # 可复现性

    overall: Mapped[float] = mapped_column(Float, default=0.0)         # 综合得分
    summary: Mapped[str | None] = mapped_column(_LongText, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    paper: Mapped["Paper"] = relationship(back_populates="scores")  # noqa: F821
    agent: Mapped["Agent"] = relationship(back_populates="scores")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Score paper={self.paper_id} agent={self.agent_id} overall={self.overall}>"
