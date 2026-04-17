from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.agent import Agent
from app.schemas.agent import AgentCreate, AgentRead

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("/", response_model=AgentRead, status_code=201)
def create_agent(payload: AgentCreate, db: Session = Depends(get_db)):
    if db.query(Agent).filter(Agent.name == payload.name).first():
        raise HTTPException(400, "Agent 名称已存在")
    agent = Agent(**payload.model_dump())
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/", response_model=list[AgentRead])
def list_agents(db: Session = Depends(get_db)):
    return db.query(Agent).all()


@router.get("/{agent_id}", response_model=AgentRead)
def get_agent(agent_id: int, db: Session = Depends(get_db)):
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")
    return agent
