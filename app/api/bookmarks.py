"""收藏功能 API（含文件夹管理）"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.auth import get_current_user, get_optional_user
from app.database import get_db
from app.models.bookmark import Bookmark
from app.models.bookmark_folder import BookmarkFolder
from app.models.paper import Paper
from app.models.user import User

router = APIRouter(tags=["bookmarks"])


# ── Schemas ───────────────────────────────────────────────

class BookmarkToggleRequest(BaseModel):
    folder_id: int | None = None


class BookmarkToggleResponse(BaseModel):
    paper_id: int
    bookmarked: bool
    bookmark_count: int


class BookmarkedPaperItem(BaseModel):
    id: int
    title: str
    cover_image_url: str | None
    core_contribution: str | None
    arxiv_id: str | None
    score_count: int
    comment_count: int
    bookmark_count: int
    bookmarked_at: datetime
    folder_id: int | None = None
    folder_name: str | None = None


class BookmarkStatus(BaseModel):
    paper_id: int
    bookmark_count: int
    bookmarked: bool


class FolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class FolderRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class FolderItem(BaseModel):
    id: int
    name: str
    count: int
    created_at: datetime


class MoveRequest(BaseModel):
    folder_id: int | None = None


# ── 文件夹 CRUD ──────────────────────────────────────────

@router.post("/bookmarks/folders", response_model=FolderItem)
def create_folder(
    body: FolderCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dup = db.query(BookmarkFolder).filter(
        BookmarkFolder.user_id == user.id, BookmarkFolder.name == body.name
    ).first()
    if dup:
        raise HTTPException(409, "文件夹已存在")
    folder = BookmarkFolder(user_id=user.id, name=body.name)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return FolderItem(id=folder.id, name=folder.name, count=0, created_at=folder.created_at)


@router.get("/bookmarks/folders", response_model=list[FolderItem])
def list_folders(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    folders = (
        db.query(BookmarkFolder)
        .filter(BookmarkFolder.user_id == user.id)
        .order_by(BookmarkFolder.created_at)
        .all()
    )
    result = []
    for f in folders:
        cnt = db.query(func.count(Bookmark.id)).filter(
            Bookmark.user_id == user.id, Bookmark.folder_id == f.id
        ).scalar() or 0
        result.append(FolderItem(id=f.id, name=f.name, count=cnt, created_at=f.created_at))
    return result


@router.put("/bookmarks/folders/{folder_id}", response_model=FolderItem)
def rename_folder(
    folder_id: int,
    body: FolderRename,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    folder = db.query(BookmarkFolder).filter(
        BookmarkFolder.id == folder_id, BookmarkFolder.user_id == user.id
    ).first()
    if not folder:
        raise HTTPException(404, "文件夹不存在")
    dup = db.query(BookmarkFolder).filter(
        BookmarkFolder.user_id == user.id, BookmarkFolder.name == body.name,
        BookmarkFolder.id != folder_id,
    ).first()
    if dup:
        raise HTTPException(409, "名称已被占用")
    folder.name = body.name
    db.commit()
    db.refresh(folder)
    cnt = db.query(func.count(Bookmark.id)).filter(
        Bookmark.user_id == user.id, Bookmark.folder_id == folder.id
    ).scalar() or 0
    return FolderItem(id=folder.id, name=folder.name, count=cnt, created_at=folder.created_at)


@router.delete("/bookmarks/folders/{folder_id}")
def delete_folder(
    folder_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    folder = db.query(BookmarkFolder).filter(
        BookmarkFolder.id == folder_id, BookmarkFolder.user_id == user.id
    ).first()
    if not folder:
        raise HTTPException(404, "文件夹不存在")
    db.query(Bookmark).filter(
        Bookmark.user_id == user.id, Bookmark.folder_id == folder_id
    ).update({Bookmark.folder_id: None})
    db.delete(folder)
    db.commit()
    return {"ok": True}


# ── 移动收藏到文件夹 ─────────────────────────────────────

@router.put("/bookmarks/{bookmark_id}/move")
def move_bookmark(
    bookmark_id: int,
    body: MoveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bm = db.query(Bookmark).filter(Bookmark.id == bookmark_id, Bookmark.user_id == user.id).first()
    if not bm:
        raise HTTPException(404, "收藏不存在")
    if body.folder_id is not None:
        folder = db.query(BookmarkFolder).filter(
            BookmarkFolder.id == body.folder_id, BookmarkFolder.user_id == user.id
        ).first()
        if not folder:
            raise HTTPException(404, "目标文件夹不存在")
    bm.folder_id = body.folder_id
    db.commit()
    return {"ok": True}


# ── 收藏 / 取消收藏 ──────────────────────────────────────

@router.post("/papers/{paper_id}/bookmark", response_model=BookmarkToggleResponse)
def toggle_bookmark(
    paper_id: int,
    body: BookmarkToggleRequest | None = Body(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    paper = db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")

    existing = db.query(Bookmark).filter(
        Bookmark.user_id == user.id, Bookmark.paper_id == paper_id
    ).first()

    if existing:
        db.delete(existing)
        db.commit()
        bookmarked = False
    else:
        folder_id = body.folder_id if body else None
        if folder_id is not None:
            folder = db.query(BookmarkFolder).filter(
                BookmarkFolder.id == folder_id, BookmarkFolder.user_id == user.id
            ).first()
            if not folder:
                raise HTTPException(404, "文件夹不存在")
        db.add(Bookmark(user_id=user.id, paper_id=paper_id, folder_id=folder_id))
        db.commit()
        bookmarked = True

    count = db.query(func.count(Bookmark.id)).filter(Bookmark.paper_id == paper_id).scalar() or 0
    return BookmarkToggleResponse(paper_id=paper_id, bookmarked=bookmarked, bookmark_count=count)


# ── 查看我的收藏 ─────────────────────────────────────────

@router.get("/bookmarks", response_model=list[BookmarkedPaperItem])
def list_my_bookmarks(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    folder_id: int | None = Query(None),
    uncategorized: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models.comment import Comment
    from app.models.score import Score

    q = db.query(Bookmark).filter(Bookmark.user_id == user.id)
    if uncategorized:
        q = q.filter(Bookmark.folder_id.is_(None))
    elif folder_id is not None:
        q = q.filter(Bookmark.folder_id == folder_id)

    rows = q.order_by(Bookmark.created_at.desc()).offset(skip).limit(limit).all()

    folder_map: dict[int, str] = {}
    if rows:
        fids = {bm.folder_id for bm in rows if bm.folder_id}
        if fids:
            for f in db.query(BookmarkFolder).filter(BookmarkFolder.id.in_(fids)).all():
                folder_map[f.id] = f.name

    items = []
    for bm in rows:
        paper = db.get(Paper, bm.paper_id)
        if not paper:
            continue
        sc = db.query(func.count(Score.id)).filter(Score.paper_id == paper.id).scalar() or 0
        cc = db.query(func.count(Comment.id)).filter(Comment.paper_id == paper.id).scalar() or 0
        bc = db.query(func.count(Bookmark.id)).filter(Bookmark.paper_id == paper.id).scalar() or 0
        items.append(BookmarkedPaperItem(
            id=paper.id,
            title=paper.title,
            cover_image_url=paper.cover_image_url,
            core_contribution=paper.core_contribution,
            arxiv_id=paper.arxiv_id,
            score_count=sc,
            comment_count=cc,
            bookmark_count=bc,
            bookmarked_at=bm.created_at,
            folder_id=bm.folder_id,
            folder_name=folder_map.get(bm.folder_id) if bm.folder_id else None,
        ))
    return items


# ── 查询某论文的收藏数 + 当前用户是否已收藏 ──────────────

@router.get("/papers/{paper_id}/bookmark", response_model=BookmarkStatus)
def get_bookmark_status(
    paper_id: int,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    count = db.query(func.count(Bookmark.id)).filter(Bookmark.paper_id == paper_id).scalar() or 0
    bookmarked = False
    if user:
        bookmarked = db.query(Bookmark).filter(
            Bookmark.user_id == user.id, Bookmark.paper_id == paper_id
        ).first() is not None
    return BookmarkStatus(paper_id=paper_id, bookmark_count=count, bookmarked=bookmarked)
