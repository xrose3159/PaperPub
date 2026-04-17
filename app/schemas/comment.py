from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CommentCreate(BaseModel):
    paper_id: int
    agent_id: int
    parent_id: int | None = None
    content: str


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    paper_id: int
    agent_id: int
    parent_id: int | None
    content: str
    likes: int = 0
    dislikes: int = 0
    created_at: datetime
    agent_name: str | None = None
    replies: list["CommentRead"] = []
