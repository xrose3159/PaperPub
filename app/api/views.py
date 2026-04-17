"""前端页面专用的聚合 API —— /api/papers 和 /api/paper/{id}"""

from __future__ import annotations

import json
import pathlib
import threading
from datetime import datetime, timedelta
from enum import Enum

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import String, asc, cast, desc, func, or_
from sqlalchemy.orm import Session, joinedload

from app.api.auth import get_current_user, get_optional_user
from app.database import get_db
from app.models.agent import Agent
from app.models.bookmark import Bookmark
from app.models.comment import Comment
from app.models.paper import Paper
from app.models.paper_like import PaperLike
from app.models.score import Score
from app.models.user import User

from app.schemas.views import (
    AgentBrief,
    AgentRadar,
    CommentNode,
    HotComment,
    PaperCard,
    PaperDetail,
    PaperListResponse,
    RadarAverage,
    UserBrief,
)


class SortMode(str, Enum):
    hot = "hot"
    new = "new"
    active = "active"
    score = "score"

router = APIRouter(tags=["frontend-views"])

DIMS = ["novelty", "rigor", "applicability", "clarity", "significance", "reproducibility"]


def _agent_brief(agent: Agent) -> AgentBrief:
    return AgentBrief(
        id=agent.id,
        name=agent.name,
        avatar=agent.avatar,
        bio=agent.bio,
        personality=agent.personality,
        model_name=agent.model_name,
    )


# ── 共用：Paper → PaperCard 转换 ────────────────────────────

def _batch_paper_cards(papers: list[Paper], db: Session, current_user_id: int | None = None) -> list[PaperCard]:
    """批量构建 PaperCard，用聚合查询代替逐篇 N+1。"""
    if not papers:
        return []

    pids = [p.id for p in papers]

    # 1) 批量聚合 avg_score + score_count
    score_agg = dict(
        db.query(Score.paper_id, func.avg(Score.overall))
        .filter(Score.paper_id.in_(pids))
        .group_by(Score.paper_id)
        .all()
    )

    # 2) 批量聚合 review_count (distinct scores per paper)
    review_agg = dict(
        db.query(Score.paper_id, func.count(Score.id))
        .filter(Score.paper_id.in_(pids))
        .group_by(Score.paper_id)
        .all()
    )

    # 3) 批量聚合 comment_count
    comment_agg = dict(
        db.query(Comment.paper_id, func.count(Comment.id))
        .filter(Comment.paper_id.in_(pids))
        .group_by(Comment.paper_id)
        .all()
    )

    # 4) 批量聚合 bookmark_count
    bookmark_agg = dict(
        db.query(Bookmark.paper_id, func.count(Bookmark.id))
        .filter(Bookmark.paper_id.in_(pids))
        .group_by(Bookmark.paper_id)
        .all()
    )

    # 5) 批量聚合 like_count
    like_agg = dict(
        db.query(PaperLike.paper_id, func.count(PaperLike.id))
        .filter(PaperLike.paper_id.in_(pids))
        .group_by(PaperLike.paper_id)
        .all()
    )

    # 6) 当前用户已点赞集合
    liked_pids: set[int] = set()
    if current_user_id:
        liked_pids = {
            row[0]
            for row in db.query(PaperLike.paper_id)
            .filter(PaperLike.paper_id.in_(pids), PaperLike.user_id == current_user_id)
            .all()
        }

    # 7) 批量拿 score→agent 映射
    all_scores = (
        db.query(Score)
        .options(joinedload(Score.agent))
        .filter(Score.paper_id.in_(pids))
        .all()
    )
    scores_by_paper: dict[int, list[Score]] = {}
    for s in all_scores:
        scores_by_paper.setdefault(s.paper_id, []).append(s)

    # 5) 批量拿热评：每篇论文回复数最多的顶层评论
    reply_count_sub = (
        db.query(Comment.parent_id, func.count(Comment.id).label("rc"))
        .filter(Comment.parent_id.isnot(None))
        .group_by(Comment.parent_id)
        .subquery()
    )
    hot_comments_raw = (
        db.query(Comment, func.coalesce(reply_count_sub.c.rc, 0).label("rc"))
        .outerjoin(reply_count_sub, Comment.id == reply_count_sub.c.parent_id)
        .options(joinedload(Comment.agent), joinedload(Comment.user))
        .filter(Comment.paper_id.in_(pids), Comment.parent_id.is_(None))
        .all()
    )
    hot_by_paper: dict[int, HotComment] = {}
    candidates: dict[int, list] = {}
    for c, rc in hot_comments_raw:
        candidates.setdefault(c.paper_id, []).append((c, rc))
    for pid, cands in candidates.items():
        cands.sort(key=lambda x: (x[1], x[0].likes, x[0].created_at.timestamp()), reverse=True)
        best, best_rc = cands[0]
        hot_by_paper[pid] = HotComment(
            content=best.content[:200],
            stance=best.stance or "medium",
            likes=best.likes,
            reply_count=best_rc,
            agent_name=best.agent.name if best.agent else None,
            agent_avatar=best.agent.avatar if best.agent else None,
            user_name=best.user.username if best.user else None,
        )

    cards = []
    for p in papers:
        pid = p.id
        avg_raw = score_agg.get(pid)
        avg = round(avg_raw, 1) if avg_raw else None
        sa = []
        for s in scores_by_paper.get(pid, []):
            if s.agent:
                sa.append(_agent_brief(s.agent))
        cards.append(PaperCard(
            id=pid, arxiv_id=p.arxiv_id, title=p.title, abstract=p.abstract,
            zh_abstract=p.zh_abstract, authors=p.authors, categories=p.categories,
            ai_category=p.ai_category, ai_tags=p.ai_tags or [],
            core_contribution=p.core_contribution,
            core_contribution_en=p.core_contribution_en,
            cover_image_url=p.cover_image_url,
            github_url=p.github_url, github_stars=p.github_stars,
            huggingface_url=p.huggingface_url, hf_likes=p.hf_likes,
            affiliations=p.affiliations,
            published_at=p.published_at,
            avg_score=avg,
            review_count=review_agg.get(pid, 0),
            comment_count=comment_agg.get(pid, 0),
            bookmark_count=bookmark_agg.get(pid, 0),
            like_count=like_agg.get(pid, 0),
            is_liked=pid in liked_pids,
            uploaded_by=p.uploaded_by,
            score_agents=sa,
            hot_comment=hot_by_paper.get(pid),
            meta_review=p.meta_review,
        ))
    return cards


def _dir(col, order: str):
    return asc(col) if order == "asc" else desc(col)


def _apply_sort(q, sort: SortMode, db: Session, order: str = "desc"):
    """给查询添加排序逻辑，返回排序后的查询。

    每种排序都追加 Paper.id 作为 tiebreaker，确保分页结果跨请求稳定。
    """
    _id_tie = _dir(Paper.id, order)

    if sort == SortMode.new:
        return q.order_by(_dir(Paper.published_at, order), _id_tie)

    if sort == SortMode.active:
        last_comment_sub = (
            db.query(Comment.paper_id, func.max(Comment.created_at).label("last_at"))
            .group_by(Comment.paper_id).subquery()
        )
        q = q.outerjoin(last_comment_sub, Paper.id == last_comment_sub.c.paper_id)
        return q.order_by(_dir(func.coalesce(last_comment_sub.c.last_at, Paper.published_at), order), _id_tie)

    if sort == SortMode.score:
        avg_sub = (
            db.query(Score.paper_id, func.avg(Score.overall).label("avg_score"))
            .group_by(Score.paper_id).subquery()
        )
        q = q.outerjoin(avg_sub, Paper.id == avg_sub.c.paper_id)
        return q.order_by(_dir(func.coalesce(avg_sub.c.avg_score, 0), order), _id_tie)

    cc_sub = db.query(Comment.paper_id, func.count(Comment.id).label("cc")).group_by(Comment.paper_id).subquery()
    sc_sub = db.query(Score.paper_id, func.count(Score.id).label("sc")).group_by(Score.paper_id).subquery()
    q = q.outerjoin(cc_sub, Paper.id == cc_sub.c.paper_id).outerjoin(sc_sub, Paper.id == sc_sub.c.paper_id)
    from app.database import age_in_hours
    age_hours = age_in_hours(Paper.published_at)
    gravity = (age_hours + 2.0) * (age_hours + 2.0)
    hot_score = (func.coalesce(cc_sub.c.cc, 0) * 1.5 + func.coalesce(sc_sub.c.sc, 0) * 1.0 + 0.1) / gravity
    return q.order_by(_dir(hot_score, order), _id_tie)


# ── 论文列表（分页） ────────────────────────────────────────

@router.get("/papers", response_model=PaperListResponse)
def list_papers_for_frontend(
    skip: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=2000),
    sort: SortMode = Query(SortMode.hot, description="排序方式: hot / new / active"),
    order: str = Query("desc", pattern="^(asc|desc)$", description="排序方向"),
    start_date: str | None = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: str | None = Query(None, description="结束日期 YYYY-MM-DD"),
    category: str | None = None,
    ai_category: str | None = Query(None, description="按 AI 智能分类过滤"),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    q = db.query(Paper)
    if category:
        q = q.filter(Paper.categories.contains(category))
    if ai_category:
        q = q.filter(Paper.ai_tags.isnot(None))
        cats = [c.strip() for c in ai_category.split(",") if c.strip()]
        for c in cats:
            q = q.filter(cast(Paper.ai_tags, String).like(f'%"{c}"%'))
    if start_date:
        q = q.filter(Paper.published_at >= datetime.strptime(start_date, "%Y-%m-%d"))
    if end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        q = q.filter(Paper.published_at <= end_dt)

    total = q.count()
    q = _apply_sort(q, sort, db, order)
    papers = q.offset(skip).limit(limit).all()
    uid = current_user.id if current_user else None
    cards = _batch_paper_cards(papers, db, current_user_id=uid)

    return PaperListResponse(items=cards, total=total, has_more=(skip + limit < total))


# ── 论文搜索（分页） ────────────────────────────────────────

@router.get("/papers/search", response_model=PaperListResponse)
def search_papers(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    skip: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=2000),
    sort: SortMode = Query(SortMode.new),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    pattern = f"%{q}%"
    query = db.query(Paper).filter(
        Paper.title.ilike(pattern) | Paper.abstract.ilike(pattern) | Paper.authors.ilike(pattern)
    )
    if start_date:
        query = query.filter(Paper.published_at >= datetime.strptime(start_date, "%Y-%m-%d"))
    if end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        query = query.filter(Paper.published_at <= end_dt)

    total = query.count()
    query = _apply_sort(query, sort, db, order)
    papers = query.offset(skip).limit(limit).all()
    uid = current_user.id if current_user else None
    cards = _batch_paper_cards(papers, db, current_user_id=uid)

    return PaperListResponse(items=cards, total=total, has_more=(skip + limit < total))


# ── 按 arXiv ID 爬取单篇论文 ──────────────────────────────────

class CrawlRequest(BaseModel):
    arxiv_id: str

class CrawlStatus(BaseModel):
    status: str
    message: str
    paper_id: int | None = None
    arxiv_id: str | None = None
    title: str | None = None

_crawl_tasks: dict[str, CrawlStatus] = {}
_user_uploads: dict[int, list[str]] = {}


def _crawl_single_paper(arxiv_id: str, user_id: int | None = None):
    """后台线程：爬取并处理单篇论文。"""
    import json, arxiv as arxiv_lib, logging
    from app.database import SessionLocal
    from app.services.arxiv_crawler import _enrich_one, _extract_arxiv_id

    logger = logging.getLogger(__name__)
    try:
        _crawl_tasks[arxiv_id] = CrawlStatus(status="processing", message="正在从 arXiv 获取论文…", arxiv_id=arxiv_id)

        clean_id = arxiv_id.strip()
        for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/",
                        "https://arxiv.org/pdf/", "http://arxiv.org/pdf/"):
            if clean_id.startswith(prefix):
                clean_id = clean_id[len(prefix):].rstrip("/").replace(".pdf", "")

        import time as _time
        client = arxiv_lib.Client(delay_seconds=3.0, num_retries=5)
        search = arxiv_lib.Search(id_list=[clean_id])
        results = None
        for attempt in range(4):
            try:
                results = list(client.results(search))
                break
            except Exception as e:
                if "429" in str(e) and attempt < 3:
                    wait = 10 * (attempt + 1)
                    logger.warning("arXiv 429 限流，%d 秒后重试 (%d/3)…", wait, attempt + 1)
                    _crawl_tasks[arxiv_id] = CrawlStatus(
                        status="processing",
                        message=f"arXiv 限流，{wait}秒后重试（{attempt+1}/3）…",
                        arxiv_id=arxiv_id,
                    )
                    _time.sleep(wait)
                else:
                    raise

        if not results:
            _crawl_tasks[arxiv_id] = CrawlStatus(status="error", message=f"未找到 arXiv 论文: {clean_id}", arxiv_id=arxiv_id)
            return

        r = results[0]
        title = r.title.replace("\n", " ").strip()
        from datetime import timezone
        pub = r.published
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)

        p = {
            "arxiv_id": _extract_arxiv_id(r.entry_id),
            "title": title,
            "abstract": r.summary.replace("\n", " ").strip(),
            "authors": json.dumps([a.name for a in r.authors], ensure_ascii=False),
            "arxiv_url": r.entry_id,
            "pdf_url": r.pdf_url,
            "categories": ",".join(r.categories),
            "published_at": pub,
        }

        _crawl_tasks[arxiv_id] = CrawlStatus(status="processing", message="正在分析论文…", arxiv_id=arxiv_id, title=title)
        p = _enrich_one(p, 1, 1)

        db = SessionLocal()
        try:
            existing = db.query(Paper).filter(Paper.arxiv_id == p["arxiv_id"]).first()
            if existing:
                if user_id and not existing.uploaded_by:
                    existing.uploaded_by = user_id
                    db.commit()
                _crawl_tasks[arxiv_id] = CrawlStatus(status="done", message="该论文已存在", paper_id=existing.id, arxiv_id=arxiv_id, title=title)
                return

            if user_id:
                p["uploaded_by"] = user_id
            paper = Paper(**p)
            db.add(paper)
            db.commit()
            db.refresh(paper)
            _crawl_tasks[arxiv_id] = CrawlStatus(status="done", message="上传完成！", paper_id=paper.id, arxiv_id=arxiv_id, title=title)
        finally:
            db.close()

    except Exception as e:
        logger.exception("爬取论文 %s 失败", arxiv_id)
        _crawl_tasks[arxiv_id] = CrawlStatus(status="error", message=f"处理失败: {e}", arxiv_id=arxiv_id)


@router.post("/papers/crawl", response_model=CrawlStatus)
def crawl_paper_by_id(
    req: CrawlRequest,
    user: User = Depends(get_current_user),
):
    arxiv_id = req.arxiv_id.strip()
    if not arxiv_id:
        raise HTTPException(400, "arxiv_id 不能为空")

    if arxiv_id in _crawl_tasks and _crawl_tasks[arxiv_id].status == "processing":
        return _crawl_tasks[arxiv_id]

    user_id = user.id
    _crawl_tasks[arxiv_id] = CrawlStatus(status="processing", message="已提交，开始上传…", arxiv_id=arxiv_id)
    threading.Thread(target=_crawl_single_paper, args=(arxiv_id, user_id), daemon=True).start()

    _user_uploads.setdefault(user_id, [])
    if arxiv_id not in _user_uploads[user_id]:
        _user_uploads[user_id].append(arxiv_id)

    return _crawl_tasks[arxiv_id]


@router.get("/papers/crawl-status/{arxiv_id:path}", response_model=CrawlStatus)
def get_crawl_status(arxiv_id: str):
    if arxiv_id in _crawl_tasks:
        return _crawl_tasks[arxiv_id]
    return CrawlStatus(status="unknown", message="没有该论文的爬取记录")


def _reenrich_paper(paper_id: int):
    """后台线程：对已存在论文重新执行增强步骤，补全缺失字段。"""
    import logging
    from app.database import SessionLocal
    from app.services.arxiv_crawler import _enrich_one

    logger = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        paper = db.get(Paper, paper_id)
        if not paper:
            logger.warning("re-enrich: paper %d not found", paper_id)
            return

        p = {
            "arxiv_id": paper.arxiv_id,
            "title": paper.title,
            "abstract": paper.abstract,
            "authors": paper.authors,
            "arxiv_url": paper.arxiv_url,
            "pdf_url": paper.pdf_url,
            "categories": paper.categories,
            "published_at": paper.published_at,
        }
        p = _enrich_one(p, 1, 1)

        # 只更新原来为空的字段，不覆盖已有数据
        fields = [
            ("cover_image_url", "cover_image_url"),
            ("zh_abstract", "zh_abstract"),
            ("core_contribution", "core_contribution"),
            ("core_contribution_en", "core_contribution_en"),
            ("ai_tags", "ai_tags"),
            ("ai_category", "ai_category"),
            ("github_url", "github_url"),
            ("github_stars", "github_stars"),
            ("huggingface_url", "huggingface_url"),
            ("hf_likes", "hf_likes"),
            ("affiliations", "affiliations"),
        ]
        changed = False
        for src_key, dst_attr in fields:
            if p.get(src_key) and not getattr(paper, dst_attr):
                setattr(paper, dst_attr, p[src_key])
                changed = True
        if changed:
            db.commit()
            logger.info("re-enrich: paper %d updated", paper_id)
    except Exception:
        logger.exception("re-enrich: paper %d failed", paper_id)
        db.rollback()
    finally:
        db.close()


@router.post("/papers/{paper_id}/reenrich", response_model=CrawlStatus)
def reenrich_paper(
    paper_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对已存在论文重新执行增强，补全缺失的封面/翻译/链接等字段。"""
    paper = db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    threading.Thread(target=_reenrich_paper, args=(paper_id,), daemon=True).start()
    return CrawlStatus(status="processing", message="已开始重新处理，请稍后刷新页面", paper_id=paper_id, arxiv_id=paper.arxiv_id, title=paper.title)


@router.get("/papers/my-uploads")
def get_my_uploads(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = user.id

    papers = db.query(Paper).filter(Paper.uploaded_by == user_id)\
        .order_by(Paper.created_at.desc()).all()
    db_arxiv_ids = {p.arxiv_id for p in papers}
    db_items = [{"arxiv_id": p.arxiv_id, "title": p.title, "paper_id": p.id, "status": "done", "created_at": p.created_at.isoformat() if p.created_at else None} for p in papers]

    pending = []
    for aid in _user_uploads.get(user_id, []):
        task = _crawl_tasks.get(aid)
        if not task:
            continue
        if task.paper_id:
            p = db.get(Paper, task.paper_id)
            if p and p.arxiv_id in db_arxiv_ids:
                continue
        if task.status == "done" and task.paper_id and task.paper_id not in {it["paper_id"] for it in db_items}:
            pending.append({"arxiv_id": aid, "title": task.title, "paper_id": task.paper_id, "status": "done"})
        elif task.status == "processing":
            pending.append({"arxiv_id": aid, "title": task.title, "paper_id": None, "status": "processing", "message": task.message})
        elif task.status == "error":
            pending.append({"arxiv_id": aid, "title": task.title, "paper_id": None, "status": "error", "message": task.message})

    return pending + db_items


# ── 论文详情 ────────────────────────────────────────────────

@router.get("/paper/{paper_id}", response_model=PaperDetail)
def get_paper_detail(paper_id: int, db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")

    # 雷达图数据（一次查询预加载 agent）
    scores = (
        db.query(Score)
        .options(joinedload(Score.agent))
        .filter(Score.paper_id == paper_id)
        .all()
    )

    if scores:
        avg = RadarAverage(**{
            d: round(sum(getattr(s, d) for s in scores) / len(scores), 1)
            for d in DIMS
        })
    else:
        avg = RadarAverage()

    radar_agents = []
    for s in scores:
        if s.agent:
            radar_agents.append(AgentRadar(
                agent=_agent_brief(s.agent),
                dimensions={d: getattr(s, d) for d in DIMS},
                overall=s.overall,
            ))

    # 评论树（一次查询全部评论，Python 内存中构建树）
    all_comments = (
        db.query(Comment)
        .options(joinedload(Comment.agent), joinedload(Comment.user))
        .filter(Comment.paper_id == paper_id)
        .order_by(Comment.created_at)
        .all()
    )
    comments_by_parent: dict[int | None, list[Comment]] = {}
    for c in all_comments:
        comments_by_parent.setdefault(c.parent_id, []).append(c)

    def _build_tree(c: Comment) -> CommentNode:
        agent = _agent_brief(c.agent) if c.agent else None
        user = UserBrief(id=c.user.id, username=c.user.username, avatar=c.user.avatar) if c.user else None
        children = comments_by_parent.get(c.id, [])
        return CommentNode(
            id=c.id, content=c.content, stance=c.stance or "medium",
            likes=c.likes, dislikes=c.dislikes, created_at=c.created_at,
            parent_id=c.parent_id, agent=agent, user=user,
            replies=[_build_tree(r) for r in children],
        )

    comment_tree = [_build_tree(c) for c in comments_by_parent.get(None, [])]

    bookmark_count = db.query(func.count(Bookmark.id)).filter(Bookmark.paper_id == paper_id).scalar() or 0

    return PaperDetail(
        id=paper.id,
        arxiv_id=paper.arxiv_id,
        title=paper.title,
        abstract=paper.abstract,
        zh_abstract=paper.zh_abstract,
        authors=paper.authors,
        arxiv_url=paper.arxiv_url,
        pdf_url=paper.pdf_url,
        cover_image_url=paper.cover_image_url,
        github_url=paper.github_url,
        github_stars=paper.github_stars,
        huggingface_url=paper.huggingface_url,
        hf_likes=paper.hf_likes,
        affiliations=paper.affiliations,
        ai_tags=paper.ai_tags or [],
        core_contribution=paper.core_contribution,
        core_contribution_en=paper.core_contribution_en,
        categories=paper.categories,
        published_at=paper.published_at,
        meta_review=paper.meta_review,
        last_meta_review_ts=paper.last_meta_review_ts,
        bookmark_count=bookmark_count,
        radar_average=avg,
        radar_agents=radar_agents,
        comments=comment_tree,
    )


# ── 平台统计 ─────────────────────────────────────────────

class PlatformStats(BaseModel):
    total_papers: int
    total_reviews: int
    total_comments: int
    total_users: int
    total_agents: int
    daily_active: int
    total_visits: int


_VISITS_FILE = pathlib.Path(__file__).resolve().parent.parent.parent / "visits.count"
_visits_lock = threading.Lock()
_visits_count: int = 0

def _bump_visits() -> int:
    global _visits_count
    with _visits_lock:
        if _visits_count == 0:
            try:
                _visits_count = int(_VISITS_FILE.read_text().strip())
            except Exception:
                _visits_count = 0
        _visits_count += 1
        if _visits_count % 10 == 0:
            _VISITS_FILE.write_text(str(_visits_count))
        return _visits_count


_stats_cache: dict | None = None
_stats_cache_ts: float = 0

@router.get("/stats", response_model=PlatformStats)
def get_platform_stats(db: Session = Depends(get_db)):
    import time as _t
    global _stats_cache, _stats_cache_ts

    total_visits = _bump_visits()

    now = _t.time()
    if _stats_cache and (now - _stats_cache_ts) < 60:
        return PlatformStats(**{**_stats_cache, "total_visits": total_visits})

    total_papers = db.query(func.count(Paper.id)).scalar() or 0
    total_reviews = db.query(func.count(Score.id)).scalar() or 0
    total_comments = db.query(func.count(Comment.id)).scalar() or 0
    total_users = db.query(func.count(User.id)).scalar() or 0
    total_agents = db.query(func.count(Agent.id)).scalar() or 0

    since = datetime.utcnow() - timedelta(hours=24)
    active_from_scores = db.query(Score.agent_id).filter(Score.created_at >= since)
    active_from_comments = db.query(Comment.agent_id).filter(Comment.created_at >= since, Comment.agent_id.isnot(None))
    daily_active = active_from_scores.union(active_from_comments).count()

    _stats_cache = {
        "total_papers": total_papers,
        "total_reviews": total_reviews,
        "total_comments": total_comments,
        "total_users": total_users,
        "total_agents": total_agents,
        "daily_active": daily_active,
    }
    _stats_cache_ts = now

    return PlatformStats(**{**_stats_cache, "total_visits": total_visits})


class RecentAgent(BaseModel):
    id: int
    name: str
    avatar: str | None
    model_name: str | None
    last_active: str


@router.get("/recent-agents", response_model=list[RecentAgent])
def get_recent_agents(limit: int = Query(12, ge=1, le=50), db: Session = Depends(get_db)):
    last_comment = (
        db.query(Comment.agent_id, func.max(Comment.created_at).label("last_at"))
        .filter(Comment.agent_id.isnot(None))
        .group_by(Comment.agent_id)
        .subquery()
    )
    rows = (
        db.query(Agent, last_comment.c.last_at)
        .join(last_comment, Agent.id == last_comment.c.agent_id)
        .order_by(desc(last_comment.c.last_at))
        .limit(limit)
        .all()
    )
    result = []
    now = datetime.utcnow()
    for agent, last_at in rows:
        if last_at:
            delta = now - last_at
            mins = int(delta.total_seconds() / 60)
            if mins < 60:
                ago = f"{max(1,mins)}m ago"
            elif mins < 1440:
                ago = f"{mins//60}h ago"
            else:
                ago = f"{mins//1440}d ago"
        else:
            ago = ""
        result.append(RecentAgent(
            id=agent.id, name=agent.name, avatar=agent.avatar,
            model_name=agent.model_name, last_active=ago,
        ))
    return result


# ── 人类用户评论 ──────────────────────────────────────────────

class HumanCommentRequest(BaseModel):
    content: str
    parent_id: int | None = None

class HumanCommentResponse(BaseModel):
    comment_id: int
    message: str


@router.post("/paper/{paper_id}/comment", response_model=HumanCommentResponse)
def post_human_comment(
    paper_id: int,
    body: HumanCommentRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    paper = db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")

    content = body.content.strip()
    if not content or len(content) > 5000:
        raise HTTPException(422, "评论内容不能为空且不超过5000字")

    if body.parent_id:
        parent = db.get(Comment, body.parent_id)
        if not parent or parent.paper_id != paper_id:
            raise HTTPException(404, "父评论不存在")

    comment = Comment(
        paper_id=paper_id,
        user_id=user.id,
        agent_id=None,
        parent_id=body.parent_id,
        content=content,
        stance="medium",
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)

    if body.parent_id:
        parent = db.get(Comment, body.parent_id)
        if parent:
            from app.api.notifications import create_notification
            if parent.agent_id:
                create_notification(
                    db,
                    recipient_id=parent.agent_id,
                    actor_user_id=user.id,
                    type="reply",
                    paper_id=paper_id,
                    comment_id=comment.id,
                )
            elif parent.user_id and parent.user_id != user.id:
                create_notification(
                    db,
                    recipient_user_id=parent.user_id,
                    actor_user_id=user.id,
                    type="reply",
                    paper_id=paper_id,
                    comment_id=comment.id,
                )

    return HumanCommentResponse(comment_id=comment.id, message="评论已发布")


@router.delete("/comment/{comment_id}")
def delete_human_comment(
    comment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comment = db.get(Comment, comment_id)
    if not comment:
        raise HTTPException(404, "评论不存在")
    if comment.user_id != user.id:
        raise HTTPException(403, "只能撤回自己的评论")
    db.delete(comment)
    db.commit()
    return {"message": "评论已撤回"}


# ── 论文点赞 ──────────────────────────────────────────────────

class LikeResponse(BaseModel):
    liked: bool
    like_count: int


@router.post("/papers/{paper_id}/like", response_model=LikeResponse)
def like_paper(
    paper_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    paper = db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")

    existing = db.query(PaperLike).filter(
        PaperLike.paper_id == paper_id,
        PaperLike.user_id == user.id,
    ).first()
    if not existing:
        db.add(PaperLike(paper_id=paper_id, user_id=user.id))
        db.commit()

    count = db.query(func.count(PaperLike.id)).filter(PaperLike.paper_id == paper_id).scalar() or 0
    return LikeResponse(liked=True, like_count=count)


@router.delete("/papers/{paper_id}/like", response_model=LikeResponse)
def unlike_paper(
    paper_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.query(PaperLike).filter(
        PaperLike.paper_id == paper_id,
        PaperLike.user_id == user.id,
    ).first()
    if existing:
        db.delete(existing)
        db.commit()

    count = db.query(func.count(PaperLike.id)).filter(PaperLike.paper_id == paper_id).scalar() or 0
    return LikeResponse(liked=False, like_count=count)


# ── 精选论文（人工指定）─────────────────────────────────────

_FEATURED_FILE = pathlib.Path(__file__).resolve().parent.parent / "data" / "featured_papers.json"


@router.get("/featured-papers", response_model=PaperListResponse)
def get_featured_papers(
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    try:
        raw_ids: list[str] = json.loads(_FEATURED_FILE.read_text(encoding="utf-8"))
    except Exception:
        raw_ids = []

    if not raw_ids:
        return PaperListResponse(items=[], total=0, has_more=False)

    import re as _re
    # strip 版本号后缀（如 1706.03762v7 → 1706.03762）
    arxiv_ids = [_re.sub(r'v\d+$', '', a.strip()) for a in raw_ids]

    uid = current_user.id if current_user else None
    papers_by_arxiv = {
        p.arxiv_id: p
        for p in db.query(Paper).filter(Paper.arxiv_id.in_(arxiv_ids)).all()
    }
    # 按配置文件顺序排列
    ordered = [papers_by_arxiv[a] for a in arxiv_ids if a in papers_by_arxiv]
    cards = _batch_paper_cards(ordered, db, current_user_id=uid)
    return PaperListResponse(items=cards, total=len(cards), has_more=False)


@router.put("/featured-papers")
def update_featured_papers(
    arxiv_ids: list[str],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "仅管理员可操作")
    _FEATURED_FILE.write_text(
        json.dumps(arxiv_ids, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"message": "已更新精选论文", "count": len(arxiv_ids)}
