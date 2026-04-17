from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PaperCreate(BaseModel):
    arxiv_id: str
    title: str
    abstract: str
    authors: str
    arxiv_url: str
    pdf_url: str | None = None
    categories: str
    published_at: datetime


class PaperRead(PaperCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class PaperBrief(BaseModel):
    """列表页用的精简版"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    arxiv_id: str
    title: str
    authors: str
    categories: str
    published_at: datetime
