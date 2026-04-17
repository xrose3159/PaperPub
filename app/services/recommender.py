"""每日论文推荐引擎：多信号融合推荐 + AI 每日总结。

信号维度及权重:
  quality        (0.30) - Agent 评分均值
  category_match (0.20) - ai_category 是否在用户兴趣列表中
  tag_similarity (0.25) - 与用户收藏论文 ai_tags 的 Jaccard 相似度
  popularity     (0.10) - 收藏数 + 评论数（对数归一化）
  recency        (0.15) - 发表时效（线性衰减）
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.bookmark import Bookmark
from app.models.comment import Comment
from app.models.daily_summary import DailySummary
from app.models.paper import Paper
from app.models.recommendation import DailyRecommendation
from app.models.score import Score
from app.models.user import User

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 7
TOP_N = 12

W_QUALITY = 0.30
W_CATEGORY = 0.20
W_TAG_SIM = 0.25
W_POPULARITY = 0.10
W_RECENCY = 0.15


def _build_user_tag_profile(db: Session, user_id: int) -> set[str]:
    """从用户收藏过的论文中提取所有 ai_tags，构建标签画像。"""
    bm_paper_ids = [
        r[0] for r in db.query(Bookmark.paper_id)
        .filter(Bookmark.user_id == user_id).all()
    ]
    if not bm_paper_ids:
        return set()
    rows = db.query(Paper.ai_tags).filter(Paper.id.in_(bm_paper_ids)).all()
    tags: set[str] = set()
    for (ai_tags,) in rows:
        if ai_tags and isinstance(ai_tags, list):
            tags.update(ai_tags)
    return tags


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def _get_candidate_stats(db: Session, since: datetime):
    """查询近期论文及其统计数据，返回 (paper_stats, bm_counts, cm_counts, max_score, max_pop_log)。"""
    paper_stats = (
        db.query(
            Paper.id,
            Paper.ai_category,
            Paper.ai_tags,
            Paper.published_at,
            sa_func.coalesce(sa_func.avg(Score.overall), 0).label("avg_score"),
        )
        .outerjoin(Score, Score.paper_id == Paper.id)
        .filter(Paper.published_at >= since)
        .group_by(Paper.id)
        .all()
    )
    if not paper_stats:
        return None, {}, {}, 1, 1

    pids = [p.id for p in paper_stats]

    bm_counts: dict[int, int] = {}
    for pid, cnt in (
        db.query(Bookmark.paper_id, sa_func.count(Bookmark.id))
        .filter(Bookmark.paper_id.in_(pids))
        .group_by(Bookmark.paper_id).all()
    ):
        bm_counts[pid] = cnt

    cm_counts: dict[int, int] = {}
    for pid, cnt in (
        db.query(Comment.paper_id, sa_func.count(Comment.id))
        .filter(Comment.paper_id.in_(pids))
        .group_by(Comment.paper_id).all()
    ):
        cm_counts[pid] = cnt

    max_score = max((p.avg_score for p in paper_stats), default=1) or 1
    max_pop_log = max(
        math.log1p(bm_counts.get(p.id, 0) + cm_counts.get(p.id, 0))
        for p in paper_stats
    ) or 1

    return paper_stats, bm_counts, cm_counts, max_score, max_pop_log


def _score_papers_for_user(
    db: Session,
    user: User,
    paper_stats,
    bm_counts: dict,
    cm_counts: dict,
    max_score: float,
    max_pop_log: float,
    today: date,
) -> list[int]:
    """为单个用户计算推荐分并返回 TOP N paper_id 列表（保证兴趣多样性）。"""
    interests = user.interests
    if not interests:
        return []

    interest_set = set(interests)
    user_tags = _build_user_tag_profile(db, user.id)

    scored: list[tuple[int, float, str | None]] = []
    for p in paper_stats:
        quality = (float(p.avg_score) / max_score) if max_score else 0
        cat_match = 1.0 if (p.ai_category and p.ai_category in interest_set) else 0.0
        paper_tags = set(p.ai_tags) if p.ai_tags and isinstance(p.ai_tags, list) else set()
        tag_sim = _jaccard(user_tags, paper_tags) if user_tags else (
            0.3 if paper_tags & interest_set else 0.0
        )
        pop_raw = bm_counts.get(p.id, 0) + cm_counts.get(p.id, 0)
        popularity = math.log1p(pop_raw) / max_pop_log if max_pop_log else 0
        days_old = (today - p.published_at.date()).days if p.published_at else LOOKBACK_DAYS
        recency = max(0.0, 1.0 - days_old / LOOKBACK_DAYS)

        rec_score = (
            W_QUALITY * quality
            + W_CATEGORY * cat_match
            + W_TAG_SIM * tag_sim
            + W_POPULARITY * popularity
            + W_RECENCY * recency
        )
        scored.append((p.id, rec_score, p.ai_category))

    scored.sort(key=lambda x: x[1], reverse=True)

    n_interests = len(interests)
    per_cat = max(1, TOP_N // n_interests)
    selected: list[int] = []
    selected_set: set[int] = set()

    for cat in interests:
        cat_papers = [(pid, sc) for pid, sc, c in scored if c == cat and pid not in selected_set]
        for pid, _ in cat_papers[:per_cat]:
            selected.append(pid)
            selected_set.add(pid)

    for pid, _, _ in scored:
        if len(selected) >= TOP_N:
            break
        if pid not in selected_set:
            selected.append(pid)
            selected_set.add(pid)

    return selected[:TOP_N]


def ensure_user_recommendations(db: Session, user: User) -> None:
    """确保该用户今日已有推荐，如果没有则即时生成。"""
    if not user.interests:
        return

    today = date.today()
    existing = (
        db.query(DailyRecommendation)
        .filter(DailyRecommendation.user_id == user.id, DailyRecommendation.rec_date == today)
        .count()
    )
    if existing > 0:
        return

    since = datetime.combine(today - timedelta(days=LOOKBACK_DAYS), datetime.min.time())
    paper_stats, bm_counts, cm_counts, max_score, max_pop_log = _get_candidate_stats(db, since)
    if not paper_stats:
        return

    top_ids = _score_papers_for_user(db, user, paper_stats, bm_counts, cm_counts, max_score, max_pop_log, today)
    for pid in top_ids:
        db.add(DailyRecommendation(user_id=user.id, paper_id=pid, rec_date=today))
    if top_ids:
        db.commit()


def generate_daily_recommendations() -> int:
    """为所有设置了兴趣的用户生成今日推荐，返回受益用户数。"""
    db = SessionLocal()
    try:
        today = date.today()
        since = datetime.combine(today - timedelta(days=LOOKBACK_DAYS), datetime.min.time())

        users = db.query(User).filter(User.interests.isnot(None)).all()
        user_count = 0

        paper_stats, bm_counts, cm_counts, max_score, max_pop_log = _get_candidate_stats(db, since)
        if not paper_stats:
            return 0

        for user in users:
            if not user.interests:
                continue

            existing = (
                db.query(DailyRecommendation)
                .filter(DailyRecommendation.user_id == user.id, DailyRecommendation.rec_date == today)
                .count()
            )
            if existing > 0:
                continue

            top_ids = _score_papers_for_user(
                db, user, paper_stats, bm_counts, cm_counts, max_score, max_pop_log, today,
            )
            for pid in top_ids:
                db.add(DailyRecommendation(user_id=user.id, paper_id=pid, rec_date=today))
            if top_ids:
                user_count += 1
                db.commit()

        return user_count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── 每日 AI 总结 ─────────────────────────────────────────────

_SUMMARY_SYSTEM_PROMPT = (
    "你是一位专业的 AI 研究日报撰写者。你的任务是根据给定的论文列表，按研究方向分组总结当天的最新研究进展。\n"
    "要求：\n"
    "1. 按研究方向用 Markdown 二级标题分组（如 ## 🏛️ Foundation）\n"
    "2. 每个方向先给出论文数量，然后概括关键主题、值得关注的方法创新和潜在影响\n"
    "3. 适当提及具体论文标题以增强可信度\n"
    "4. 每个方向 150-300 字，总字数根据方向数量自适应\n"
    "5. 使用中文撰写，语言简洁专业\n"
    "6. 最后用一段简短的「今日看点」总结全天亮点"
)


def _build_summary_prompt(papers_by_cat: dict[str, list[dict]], target_date: date, total_count: int) -> str:
    """构建发给 LLM 的论文总结 user prompt。"""
    lines = [f"以下是 {target_date.isoformat()} 新入库的、与用户研究兴趣相关的全部 {total_count} 篇论文（按研究方向分组）：\n"]
    for cat, papers in papers_by_cat.items():
        lines.append(f"## {cat}（{len(papers)} 篇）")
        for i, p in enumerate(papers, 1):
            lines.append(f"{i}. **{p['title']}**\n   {p['abstract']}\n")
        lines.append("")
    return "\n".join(lines)


def generate_daily_summary_for_user(db: Session, user: User, target_date: date) -> DailySummary | None:
    """为指定用户在指定日期生成 AI 总结。

    总结范围：当天新入库（created_at）的所有论文中，ai_category 属于用户
    兴趣列表的全部论文，而非仅推荐的 10 篇。
    """
    if not user.interests:
        return None

    interest_set = set(user.interests)

    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

    papers = (
        db.query(Paper)
        .filter(
            Paper.created_at >= day_start,
            Paper.created_at < day_end,
            Paper.ai_category.in_(interest_set),
        )
        .order_by(Paper.created_at.desc())
        .all()
    )

    if not papers:
        return None

    papers_by_cat: dict[str, list[dict]] = defaultdict(list)
    for paper in papers:
        cat = paper.ai_category or "Other"
        papers_by_cat[cat].append({
            "title": paper.title,
            "abstract": paper.abstract or "",
        })

    user_prompt = _build_summary_prompt(dict(papers_by_cat), target_date, len(papers))

    try:
        from app.services.llm_client import chat
        content = chat(
            system=_SUMMARY_SYSTEM_PROMPT,
            user=user_prompt,
            model="qwen3.5-plus",
            temperature=0.5,
            max_tokens=4096,
        )
    except Exception:
        logger.exception("为用户 %s 生成每日总结时 LLM 调用失败", user.username)
        return None

    summary = DailySummary(
        user_id=user.id,
        summary_date=target_date,
        content=content,
    )
    db.add(summary)
    db.commit()
    db.refresh(summary)
    logger.info(
        "已为用户 %s 生成 %s 的每日总结（%d 篇论文，%d 字）",
        user.username, target_date, len(papers), len(content),
    )
    return summary


def generate_daily_summaries() -> int:
    """为所有设置了兴趣的用户生成今日 AI 总结，返回成功用户数。"""
    db = SessionLocal()
    try:
        today = date.today()
        users = db.query(User).filter(User.interests.isnot(None)).all()
        count = 0
        for user in users:
            if not user.interests:
                continue
            existing = (
                db.query(DailySummary)
                .filter(DailySummary.user_id == user.id, DailySummary.summary_date == today)
                .first()
            )
            if existing:
                continue
            result = generate_daily_summary_for_user(db, user, today)
            if result:
                count += 1
        return count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
