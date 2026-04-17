from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentCreate(BaseModel):
    name: str
    avatar: str | None = None
    bio: str | None = None
    system_prompt: str
    focus_areas: str
    personality: str | None = None


class AgentRead(AgentCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
