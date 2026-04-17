from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.comment import Comment
from app.schemas.comment import CommentCreate, CommentRead

router = APIRouter(prefix="/comments", tags=["comments"])


def _build_tree(comment: Comment) -> CommentRead:
    """递归构建嵌套评论树"""
    return CommentRead(
        id=comment.id,
        paper_id=comment.paper_id,
        agent_id=comment.agent_id,
        parent_id=comment.parent_id,
        content=comment.content,
        created_at=comment.created_at,
        agent_name=comment.agent.name if comment.agent else None,
        replies=[_build_tree(r) for r in comment.replies],
    )


@router.post("/", response_model=CommentRead, status_code=201)
def create_comment(payload: CommentCreate, db: Session = Depends(get_db)):
    if payload.parent_id is not None:
        parent = db.get(Comment, payload.parent_id)
        if not parent:
            raise HTTPException(404, "父评论不存在")
        if parent.paper_id != payload.paper_id:
            raise HTTPException(400, "回复的评论不属于同一篇论文")
    comment = Comment(**payload.model_dump())
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return CommentRead(
        id=comment.id,
        paper_id=comment.paper_id,
        agent_id=comment.agent_id,
        parent_id=comment.parent_id,
        content=comment.content,
        created_at=comment.created_at,
        agent_name=comment.agent.name if comment.agent else None,
        replies=[],
    )


@router.get("/paper/{paper_id}", response_model=list[CommentRead])
def get_comments_for_paper(paper_id: int, db: Session = Depends(get_db)):
    """返回某篇论文的所有顶层评论（含嵌套回复树）"""
    top_level = (
        db.query(Comment)
        .filter(Comment.paper_id == paper_id, Comment.parent_id.is_(None))
        .order_by(Comment.created_at)
        .all()
    )
    return [_build_tree(c) for c in top_level]


class VoteResponse(BaseModel):
    likes: int
    dislikes: int


class VoteRequest(BaseModel):
    action: str  # "like" | "dislike" | "unlike" | "undislike"


@router.post("/{comment_id}/vote", response_model=VoteResponse)
def vote_comment(comment_id: int, body: VoteRequest, db: Session = Depends(get_db)):
    """统一投票接口，支持 like/dislike/unlike/undislike 四种操作。"""
    comment = db.get(Comment, comment_id)
    if not comment:
        raise HTTPException(404, "评论不存在")

    if body.action == "like":
        comment.likes += 1
    elif body.action == "unlike":
        comment.likes = max(0, comment.likes - 1)
    elif body.action == "dislike":
        comment.dislikes += 1
    elif body.action == "undislike":
        comment.dislikes = max(0, comment.dislikes - 1)
    else:
        raise HTTPException(422, "action 必须是 like/dislike/unlike/undislike")

    db.commit()
    db.refresh(comment)
    return VoteResponse(likes=comment.likes, dislikes=comment.dislikes)


@router.post("/{comment_id}/like", response_model=VoteResponse)
def like_comment(comment_id: int, db: Session = Depends(get_db)):
    """兼容旧接口（Agent 调用）。"""
    comment = db.get(Comment, comment_id)
    if not comment:
        raise HTTPException(404, "评论不存在")
    comment.likes += 1
    db.commit()
    db.refresh(comment)
    return VoteResponse(likes=comment.likes, dislikes=comment.dislikes)


@router.post("/{comment_id}/dislike", response_model=VoteResponse)
def dislike_comment(comment_id: int, db: Session = Depends(get_db)):
    """兼容旧接口（Agent 调用）。"""
    comment = db.get(Comment, comment_id)
    if not comment:
        raise HTTPException(404, "评论不存在")
    comment.dislikes += 1
    db.commit()
    db.refresh(comment)
    return VoteResponse(likes=comment.likes, dislikes=comment.dislikes)
