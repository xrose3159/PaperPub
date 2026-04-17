from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.agent import Agent
from app.models.score import Score
from app.schemas.score import RadarData, ScoreCreate, ScoreRead

router = APIRouter(prefix="/scores", tags=["scores"])


@router.post("/", response_model=ScoreRead, status_code=201)
def create_score(payload: ScoreCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(Score)
        .filter(Score.paper_id == payload.paper_id, Score.agent_id == payload.agent_id)
        .first()
    )
    if existing:
        raise HTTPException(400, "该 Agent 已为此论文打分")
    score = Score(**payload.model_dump())
    db.add(score)
    db.commit()
    db.refresh(score)
    return score


@router.get("/paper/{paper_id}", response_model=list[ScoreRead])
def get_scores_for_paper(paper_id: int, db: Session = Depends(get_db)):
    return db.query(Score).filter(Score.paper_id == paper_id).all()


@router.get("/paper/{paper_id}/radar", response_model=list[RadarData])
def get_radar_data(paper_id: int, db: Session = Depends(get_db)):
    """返回某篇论文所有 Agent 打分的雷达图数据"""
    scores = db.query(Score).filter(Score.paper_id == paper_id).all()
    result = []
    for s in scores:
        agent = db.get(Agent, s.agent_id)
        result.append(RadarData(
            agent_name=agent.name if agent else "Unknown",
            dimensions={
                "novelty": s.novelty,
                "rigor": s.rigor,
                "applicability": s.applicability,
                "clarity": s.clarity,
                "significance": s.significance,
                "reproducibility": s.reproducibility,
            },
        ))
    return result
