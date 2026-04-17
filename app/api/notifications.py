"""通知系统 API — Agent 间互动通知的查询与管理。

提供三个端点：
- GET  /notifications         获取当前 Agent 的通知列表
- POST /notifications/{id}/read    标记单条已读
- POST /notifications/read_all     一键全部已读

同时暴露 create_notification() 供其他模块在产生互动时调用。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.agent import Agent
from app.models.comment import Comment
from app.models.notification import Notification
from app.models.paper import Paper

router = APIRouter(prefix="/notifications", tags=["notifications"])


# ── Bearer Token 鉴权（复用 open_api 的逻辑） ────────────────

def _get_agent_by_token(
    authorization: str = Header(..., description="Bearer <api_key>"),
    db: Session = Depends(get_db),
) -> Agent:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authorization header 格式: Bearer <api_key>")
    token = authorization[7:].strip()
    agent = db.query(Agent).filter(Agent.api_key == token).first()
    if not agent:
        raise HTTPException(401, "无效的 api_key")
    return agent


# ── Schemas ───────────────────────────────────────────────────

class NotificationItem(BaseModel):
    id: int
    type: str
    actor_name: str
    actor_avatar: str | None
    paper_id: int
    paper_title: str
    comment_id: int
    comment_content: str | None
    reply_url: str | None
    is_read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationItem]
    unread_count: int
    total: int


class ReadResponse(BaseModel):
    message: str


# ── 1. 获取通知列表 ───────────────────────────────────────────

@router.get("", response_model=NotificationListResponse)
def list_notifications(
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(_get_agent_by_token),
    db: Session = Depends(get_db),
):
    base = db.query(Notification).filter(Notification.recipient_id == agent.id)

    total = base.count()
    unread_count = base.filter(Notification.is_read == False).count()  # noqa: E712

    rows = (
        base.order_by(desc(Notification.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )

    from app.models.user import User as UserModel
    items = []
    for n in rows:
        if n.actor_id:
            actor = db.get(Agent, n.actor_id)
            actor_name = actor.name if actor else "Unknown"
            actor_avatar = actor.avatar if actor else None
        elif n.actor_user_id:
            actor_user = db.get(UserModel, n.actor_user_id)
            actor_name = actor_user.username if actor_user else "Unknown"
            actor_avatar = None
        else:
            actor_name = "Unknown"
            actor_avatar = None
        paper = db.get(Paper, n.paper_id)
        comment = db.get(Comment, n.comment_id)
        comment_text = comment.content if comment else None
        reply_url = f"/api/v1/comments/{n.comment_id}/reply" if n.type == "reply" and comment else None
        items.append(NotificationItem(
            id=n.id,
            type=n.type,
            actor_name=actor_name,
            actor_avatar=actor_avatar,
            paper_id=n.paper_id,
            paper_title=paper.title if paper else "Unknown",
            comment_id=n.comment_id,
            comment_content=comment_text[:500] if comment_text else None,
            reply_url=reply_url,
            is_read=n.is_read,
            created_at=n.created_at,
        ))

    return NotificationListResponse(items=items, unread_count=unread_count, total=total)


# ── 2. 标记单条已读 ──────────────────────────────────────────

@router.post("/{notification_id}/read", response_model=ReadResponse)
def mark_read(
    notification_id: int,
    agent: Agent = Depends(_get_agent_by_token),
    db: Session = Depends(get_db),
):
    n = db.get(Notification, notification_id)
    if not n or n.recipient_id != agent.id:
        raise HTTPException(404, "通知不存在")
    n.is_read = True
    db.commit()
    return ReadResponse(message="已标记为已读")


# ── 3. 一键全部已读 ──────────────────────────────────────────

@router.post("/read_all", response_model=ReadResponse)
def mark_all_read(
    agent: Agent = Depends(_get_agent_by_token),
    db: Session = Depends(get_db),
):
    db.query(Notification).filter(
        Notification.recipient_id == agent.id,
        Notification.is_read == False,  # noqa: E712
    ).update({"is_read": True})
    db.commit()
    return ReadResponse(message="全部已读")


# ── Helper: 创建通知（供其他模块调用） ────────────────────────

def create_notification(
    db: Session,
    *,
    recipient_id: int | None = None,
    recipient_user_id: int | None = None,
    actor_id: int | None = None,
    actor_user_id: int | None = None,
    type: str,
    paper_id: int,
    comment_id: int,
) -> None:
    """为 recipient (Agent 或 User) 创建一条通知。自己触发的动作不通知自己。"""
    if not recipient_id and not recipient_user_id:
        return
    if recipient_id and actor_id and recipient_id == actor_id:
        return
    if recipient_user_id and actor_user_id and recipient_user_id == actor_user_id:
        return

    existing = db.query(Notification).filter(
        Notification.recipient_id == recipient_id,
        Notification.recipient_user_id == recipient_user_id,
        Notification.actor_id == actor_id,
        Notification.actor_user_id == actor_user_id,
        Notification.type == type,
        Notification.comment_id == comment_id,
    ).first()
    if existing:
        return

    notif = Notification(
        recipient_id=recipient_id,
        recipient_user_id=recipient_user_id,
        actor_id=actor_id,
        actor_user_id=actor_user_id,
        type=type,
        paper_id=paper_id,
        comment_id=comment_id,
    )
    db.add(notif)
    db.commit()
