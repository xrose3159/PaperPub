from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.paper import Paper
from app.schemas.paper import PaperBrief, PaperCreate, PaperRead

router = APIRouter(prefix="/papers", tags=["papers"])


@router.post("/", response_model=PaperRead, status_code=201)
def create_paper(payload: PaperCreate, db: Session = Depends(get_db)):
    if db.query(Paper).filter(Paper.arxiv_id == payload.arxiv_id).first():
        raise HTTPException(400, "该论文已存在")
    paper = Paper(**payload.model_dump())
    db.add(paper)
    db.commit()
    db.refresh(paper)
    return paper


@router.get("/", response_model=list[PaperBrief])
def list_papers(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=2000),
    category: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Paper)
    if category:
        q = q.filter(Paper.categories.contains(category))
    return q.order_by(desc(Paper.published_at)).offset(skip).limit(limit).all()


@router.get("/{paper_id}", response_model=PaperRead)
def get_paper(paper_id: int, db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    return paper
