from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ScoreCreate(BaseModel):
    paper_id: int
    agent_id: int
    novelty: float = Field(ge=0, le=10)
    rigor: float = Field(ge=0, le=10)
    applicability: float = Field(ge=0, le=10)
    clarity: float = Field(ge=0, le=10)
    significance: float = Field(ge=0, le=10)
    reproducibility: float = Field(ge=0, le=10)
    overall: float = Field(ge=0, le=10)
    summary: str | None = None


class ScoreRead(ScoreCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class RadarData(BaseModel):
    """雷达图数据"""
    agent_name: str
    dimensions: dict[str, float]
