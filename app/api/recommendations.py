"""每日推荐 API：返回当前用户指定日期的推荐论文 + AI 总结。"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func as sa_func

from app.api.auth import get_current_user
from app.database import get_db
from app.models.daily_summary import DailySummary
from app.models.paper import Paper
from app.models.recommendation import DailyRecommendation
from app.models.score import Score
from app.models.user import User

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


class RecommendedPaper(BaseModel):
    id: int
    arxiv_id: str
    title: str
    abstract: str
    cover_image_url: str | None = None
    categories: str
    ai_category: str | None = None
    ai_tags: list | None = None
    avg_score: float = 0.0
    arxiv_url: str


class TodayDigest(BaseModel):
    date: str
    papers: list[RecommendedPaper] = Field(default_factory=list)
    summary: str | None = None


class AvailableDatesResponse(BaseModel):
    dates: list[str]


def _fetch_recommendations(db: Session, user: User, target_date: date) -> TodayDigest:
    recs = (
        db.query(DailyRecommendation, Paper)
        .join(Paper, DailyRecommendation.paper_id == Paper.id)
        .filter(
            DailyRecommendation.user_id == user.id,
            DailyRecommendation.rec_date == target_date,
        )
        .all()
    )

    papers = []
    for rec, paper in recs:
        avg = (
            db.query(sa_func.avg(Score.overall))
            .filter(Score.paper_id == paper.id)
            .scalar()
        ) or 0.0
        papers.append(RecommendedPaper(
            id=paper.id,
            arxiv_id=paper.arxiv_id,
            title=paper.title,
            abstract=paper.abstract[:200] + "..." if len(paper.abstract) > 200 else paper.abstract,
            cover_image_url=paper.cover_image_url,
            categories=paper.categories,
            ai_category=paper.ai_category,
            ai_tags=paper.ai_tags,
            avg_score=round(float(avg), 1),
            arxiv_url=paper.arxiv_url,
        ))

    papers.sort(key=lambda p: p.avg_score, reverse=True)

    summary_row = (
        db.query(DailySummary)
        .filter(DailySummary.user_id == user.id, DailySummary.summary_date == target_date)
        .first()
    )

    return TodayDigest(
        date=target_date.isoformat(),
        papers=papers,
        summary=summary_row.content if summary_row else None,
    )


@router.get("/today", response_model=TodayDigest)
def get_today_recommendations(
    target_date: str | None = Query(None, alias="date"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if target_date:
        try:
            d = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            d = date.today()
    else:
        d = date.today()

    if d == date.today():
        from app.services.recommender import ensure_user_recommendations
        ensure_user_recommendations(db, user)

    return _fetch_recommendations(db, user, d)


@router.get("/dates", response_model=AvailableDatesResponse)
def get_available_dates(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(DailyRecommendation.rec_date)
        .filter(DailyRecommendation.user_id == user.id)
        .distinct()
        .order_by(DailyRecommendation.rec_date.desc())
        .limit(30)
        .all()
    )
    return AvailableDatesResponse(dates=[r[0].isoformat() for r in rows])
