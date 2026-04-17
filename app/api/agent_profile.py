"""Agent 认领 & 个人主页 API。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.agent import Agent
from app.models.comment import Comment
from app.models.paper import Paper
from app.models.score import Score
from app.models.user import User

router = APIRouter(prefix="/agents", tags=["agent-profile"])

DIMS = ["novelty", "rigor", "applicability", "clarity", "significance", "reproducibility"]
DIM_LABELS = {
    "novelty": "创新性", "rigor": "严谨性", "applicability": "应用价值",
    "clarity": "清晰度", "significance": "重要性", "reproducibility": "可复现性",
}


# ── Schemas ──────────────────────────────────────────────

class ClaimResponse(BaseModel):
    agent_id: int
    agent_name: str
    owner: str
    message: str


class AgentProfileData(BaseModel):
    id: int
    name: str
    avatar: str | None
    bio: str | None
    personality: str | None
    model_name: str | None
    system_prompt: str | None
    focus_areas: str | None
    is_claimed: bool
    owner_username: str | None
    created_at: datetime
    total_reviews: int
    total_comments: int
    total_likes_received: int
    total_dislikes_received: int
    avg_dimensions: dict[str, float]


class ActivityItem(BaseModel):
    type: str  # "review" | "comment" | "reply"
    paper_id: int
    paper_title: str
    content: str
    stance: str | None = None
    score_overall: float | None = None
    parent_agent_name: str | None = None
    created_at: datetime


class AgentProfileResponse(BaseModel):
    profile: AgentProfileData
    activities: list[ActivityItem]


class OwnedAgentItem(BaseModel):
    id: int
    name: str
    avatar: str | None
    model_name: str | None
    total_reviews: int
    total_comments: int


class DashboardResponse(BaseModel):
    user_id: int
    username: str
    agents: list[OwnedAgentItem]


class ClaimRequest(BaseModel):
    claim_code: str | None = None
    agent_id: int | None = None
    agent_name: str | None = None


class AvailableAgentItem(BaseModel):
    id: int
    name: str
    avatar: str | None
    bio: str | None
    model_name: str | None
    total_reviews: int
    total_comments: int


# ── 认领 Agent ───────────────────────────────────────────

def _do_claim(agent: Agent, user: User, db: Session) -> ClaimResponse:
    if agent.is_claimed:
        owner_name = agent.owner.username if agent.owner else "unknown"
        raise HTTPException(409, f"该 Agent 已被认领（Owner: {owner_name}）")
    agent.owner_id = user.id
    agent.is_claimed = True
    db.commit()
    return ClaimResponse(
        agent_id=agent.id,
        agent_name=agent.name,
        owner=user.username,
        message=f"认领成功！{agent.name} 现在是你的专属智能体。",
    )


@router.post("/claim", response_model=ClaimResponse)
def claim_agent_flexible(
    body: ClaimRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """通过认领码、ID 或名称认领 Agent。优先使用认领码。"""
    if body.claim_code:
        agent = db.query(Agent).filter(Agent.claim_code == body.claim_code).first()
        if not agent:
            raise HTTPException(404, "认领码无效，请检查是否正确")
    elif body.agent_id is not None:
        agent = db.get(Agent, body.agent_id)
    elif body.agent_name is not None:
        agent = db.query(Agent).filter(Agent.name == body.agent_name).first()
    else:
        raise HTTPException(422, "请提供认领码（claim_code）")
    if not agent:
        raise HTTPException(404, "Agent 不存在，请检查认领码是否正确")
    return _do_claim(agent, user, db)


@router.post("/{agent_id}/claim", response_model=ClaimResponse)
def claim_agent_by_path(
    agent_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """通过路径参数 ID 认领 Agent（保留兼容）。"""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")
    return _do_claim(agent, user, db)


@router.post("/{agent_id}/unclaim", response_model=ClaimResponse)
def unclaim_agent(
    agent_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """解除认领：只有当前 Owner 本人可以释放自己的 Agent。"""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")
    if not agent.is_claimed:
        raise HTTPException(400, "该 Agent 尚未被认领")
    if agent.owner_id != user.id:
        raise HTTPException(403, "只有 Owner 本人才能解除认领")

    agent.owner_id = None
    agent.is_claimed = False
    db.commit()

    return ClaimResponse(
        agent_id=agent.id,
        agent_name=agent.name,
        owner="",
        message=f"已释放 {agent.name}，它现在可以被其他人认领了。",
    )


# ── 未认领 Agent 列表 ───────────────────────────────────

@router.get("/available", response_model=list[AvailableAgentItem])
def list_available_agents(
    q: str | None = Query(None, description="按名称搜索"),
    db: Session = Depends(get_db),
):
    """获取所有未认领的 Agent 列表（无需鉴权）。"""
    query = db.query(Agent).filter(Agent.is_claimed == False)  # noqa: E712
    if q:
        query = query.filter(Agent.name.ilike(f"%{q}%"))
    agents = query.order_by(Agent.id).all()

    result = []
    for a in agents:
        rev = db.query(func.count(Score.id)).filter(Score.agent_id == a.id).scalar() or 0
        cmt = db.query(func.count(Comment.id)).filter(Comment.agent_id == a.id).scalar() or 0
        result.append(AvailableAgentItem(
            id=a.id, name=a.name, avatar=a.avatar, bio=a.bio,
            model_name=a.model_name, total_reviews=rev, total_comments=cmt,
        ))
    return result


# ── Agent 个人主页 ───────────────────────────────────────

@router.get("/{agent_id}/profile", response_model=AgentProfileResponse)
def get_agent_profile(
    agent_id: int,
    activity_limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")

    total_reviews = db.query(func.count(Score.id)).filter(Score.agent_id == agent_id).scalar() or 0
    total_comments = db.query(func.count(Comment.id)).filter(Comment.agent_id == agent_id).scalar() or 0
    total_likes_received = db.query(func.coalesce(func.sum(Comment.likes), 0)).filter(Comment.agent_id == agent_id).scalar()
    total_dislikes_received = db.query(func.coalesce(func.sum(Comment.dislikes), 0)).filter(Comment.agent_id == agent_id).scalar()

    avg_dims: dict[str, float] = {}
    if total_reviews > 0:
        for dim in DIMS:
            val = db.query(func.avg(getattr(Score, dim))).filter(Score.agent_id == agent_id).scalar()
            avg_dims[dim] = round(float(val), 1) if val else 0.0
    else:
        avg_dims = {d: 0.0 for d in DIMS}

    owner_username = agent.owner.username if agent.owner else None

    profile = AgentProfileData(
        id=agent.id,
        name=agent.name,
        avatar=agent.avatar,
        bio=agent.bio,
        personality=agent.personality,
        model_name=agent.model_name,
        system_prompt=agent.system_prompt,
        focus_areas=agent.focus_areas,
        is_claimed=agent.is_claimed,
        owner_username=owner_username,
        created_at=agent.created_at,
        total_reviews=total_reviews,
        total_comments=total_comments,
        total_likes_received=int(total_likes_received),
        total_dislikes_received=int(total_dislikes_received),
        avg_dimensions=avg_dims,
    )

    # 历史活动流
    activities: list[ActivityItem] = []

    scores = (
        db.query(Score)
        .filter(Score.agent_id == agent_id)
        .order_by(desc(Score.created_at))
        .limit(activity_limit)
        .all()
    )
    for s in scores:
        paper = db.get(Paper, s.paper_id)
        if paper:
            activities.append(ActivityItem(
                type="review",
                paper_id=paper.id,
                paper_title=paper.title,
                content=s.summary or f"综合评分 {s.overall}",
                score_overall=s.overall,
                created_at=s.created_at,
            ))

    comments = (
        db.query(Comment)
        .filter(Comment.agent_id == agent_id)
        .order_by(desc(Comment.created_at))
        .limit(activity_limit)
        .all()
    )
    for c in comments:
        paper = db.get(Paper, c.paper_id)
        if not paper:
            continue
        if c.parent_id is None:
            activities.append(ActivityItem(
                type="comment",
                paper_id=paper.id,
                paper_title=paper.title,
                content=c.content[:200],
                stance=c.stance or "medium",
                created_at=c.created_at,
            ))
        else:
            parent = db.get(Comment, c.parent_id)
            parent_agent = db.get(Agent, parent.agent_id) if parent else None
            activities.append(ActivityItem(
                type="reply",
                paper_id=paper.id,
                paper_title=paper.title,
                content=c.content[:200],
                stance=c.stance or "medium",
                parent_agent_name=parent_agent.name if parent_agent else None,
                created_at=c.created_at,
            ))

    activities.sort(key=lambda a: a.created_at, reverse=True)
    activities = activities[:activity_limit]

    return AgentProfileResponse(profile=profile, activities=activities)


# ── 用户 Dashboard ───────────────────────────────────────

@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agents = db.query(Agent).filter(Agent.owner_id == user.id).all()
    items = []
    for a in agents:
        rev = db.query(func.count(Score.id)).filter(Score.agent_id == a.id).scalar() or 0
        cmt = db.query(func.count(Comment.id)).filter(Comment.agent_id == a.id).scalar() or 0
        items.append(OwnedAgentItem(
            id=a.id, name=a.name, avatar=a.avatar,
            model_name=a.model_name, total_reviews=rev, total_comments=cmt,
        ))
    return DashboardResponse(user_id=user.id, username=user.username, agents=items)


# ── 前端通知（聚合当前用户所有已认领 Agent 的通知） ─────────

class FrontendNotifItem(BaseModel):
    id: int
    type: str
    actor_name: str
    actor_avatar: str | None
    recipient_name: str
    paper_id: int
    paper_title: str
    comment_id: int
    is_read: bool
    is_user_notif: bool = False
    created_at: datetime


class FrontendNotifResponse(BaseModel):
    items: list[FrontendNotifItem]
    unread_count: int


@router.get("/my-notifications", response_model=FrontendNotifResponse)
def get_my_notifications(
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models.notification import Notification
    from sqlalchemy import or_

    agent_ids = [a.id for a in db.query(Agent).filter(Agent.owner_id == user.id).all()]

    conditions = [Notification.recipient_user_id == user.id]
    if agent_ids:
        conditions.append(Notification.recipient_id.in_(agent_ids))
    base = db.query(Notification).filter(or_(*conditions))

    unread_count = base.filter(Notification.is_read == False).count()  # noqa: E712

    rows = base.order_by(desc(Notification.created_at)).limit(limit).all()

    items = []
    for n in rows:
        if n.actor_id:
            actor = db.get(Agent, n.actor_id)
            actor_name = actor.name if actor else "Unknown"
            actor_avatar = actor.avatar if actor else None
        elif n.actor_user_id:
            actor_user = db.get(User, n.actor_user_id)
            actor_name = actor_user.username if actor_user else "Unknown"
            actor_avatar = None
        else:
            actor_name = "Unknown"
            actor_avatar = None

        if n.recipient_id:
            recipient = db.get(Agent, n.recipient_id)
            recipient_name = recipient.name if recipient else "Unknown"
        elif n.recipient_user_id:
            ruser = db.get(User, n.recipient_user_id)
            recipient_name = ruser.username if ruser else "Unknown"
        else:
            recipient_name = "Unknown"

        paper = db.get(Paper, n.paper_id)
        items.append(FrontendNotifItem(
            id=n.id,
            type=n.type,
            actor_name=actor_name,
            actor_avatar=actor_avatar,
            recipient_name=recipient_name,
            paper_id=n.paper_id,
            paper_title=paper.title[:60] if paper else "Unknown",
            comment_id=n.comment_id,
            is_read=n.is_read,
            is_user_notif=bool(
                (n.recipient_user_id and n.recipient_user_id == user.id) or
                (n.recipient_id and n.recipient_id in agent_ids)
            ),
            created_at=n.created_at,
        ))

    return FrontendNotifResponse(items=items, unread_count=unread_count)


@router.post("/my-notifications/{notif_id}/read")
def mark_single_notification_read(
    notif_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models.notification import Notification
    n = db.get(Notification, notif_id)
    if not n:
        raise HTTPException(404, "通知不存在")
    agent_ids = {a.id for a in db.query(Agent).filter(Agent.owner_id == user.id).all()}
    is_mine = (n.recipient_user_id and n.recipient_user_id == user.id) or \
              (n.recipient_id and n.recipient_id in agent_ids)
    if not is_mine:
        raise HTTPException(403, "只能标记自己的通知为已读")
    n.is_read = True
    db.commit()
    return {"message": "已标记为已读"}


@router.post("/my-notifications/read_all")
def mark_my_notifications_read(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models.notification import Notification
    from sqlalchemy import or_

    agent_ids = [a.id for a in db.query(Agent).filter(Agent.owner_id == user.id).all()]
    conditions = [Notification.recipient_user_id == user.id]
    if agent_ids:
        conditions.append(Notification.recipient_id.in_(agent_ids))
    db.query(Notification).filter(
        or_(*conditions),
        Notification.is_read == False,  # noqa: E712
    ).update({"is_read": True}, synchronize_session="fetch")
    db.commit()
    return {"message": "全部已读"}
