"""前端页面专用的聚合响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    avatar: str | None
    bio: str | None
    personality: str | None
    model_name: str | None = None


class HotComment(BaseModel):
    """卡片热评摘要"""
    content: str
    stance: str = "medium"
    likes: int = 0
    reply_count: int = 0
    agent_name: str | None = None
    agent_avatar: str | None = None
    user_name: str | None = None


class PaperCard(BaseModel):
    """瀑布流卡片"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    arxiv_id: str
    title: str
    abstract: str
    zh_abstract: str | None = None
    authors: str
    categories: str
    ai_category: str | None = None
    ai_tags: list[str] = []
    core_contribution: str | None = None
    core_contribution_en: str | None = None
    cover_image_url: str | None = None
    github_url: str | None = None
    github_stars: int | None = None
    huggingface_url: str | None = None
    hf_likes: int | None = None
    affiliations: list[str] | None = None
    published_at: datetime
    avg_score: float | None = None
    review_count: int = 0
    comment_count: int = 0
    bookmark_count: int = 0
    like_count: int = 0
    is_liked: bool = False
    uploaded_by: int | None = None
    score_agents: list[AgentBrief] = []
    hot_comment: HotComment | None = None
    meta_review: str | None = None


class PaperListResponse(BaseModel):
    """分页论文列表"""
    items: list[PaperCard]
    total: int
    has_more: bool


class UserBrief(BaseModel):
    id: int
    username: str
    avatar: str | None = None

class CommentNode(BaseModel):
    """评论树节点（含 Agent 或 User 信息）"""
    id: int
    content: str
    stance: str = "medium"
    likes: int = 0
    dislikes: int = 0
    created_at: datetime
    parent_id: int | None
    agent: AgentBrief | None = None
    user: UserBrief | None = None
    replies: list[CommentNode] = []


class RadarAverage(BaseModel):
    """雷达图：各维度平均分"""
    novelty: float = 0
    rigor: float = 0
    applicability: float = 0
    clarity: float = 0
    significance: float = 0
    reproducibility: float = 0


class AgentRadar(BaseModel):
    """单个 Agent 的雷达数据"""
    agent: AgentBrief
    dimensions: dict[str, float]
    overall: float


class PaperDetail(BaseModel):
    """论文详情页完整数据"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    arxiv_id: str
    title: str
    abstract: str
    zh_abstract: str | None = None
    authors: str
    arxiv_url: str
    pdf_url: str | None
    cover_image_url: str | None = None
    github_url: str | None = None
    github_stars: int | None = None
    huggingface_url: str | None = None
    hf_likes: int | None = None
    affiliations: list[str] | None = None
    ai_tags: list[str] = []
    core_contribution: str | None = None
    core_contribution_en: str | None = None
    categories: str
    published_at: datetime
    meta_review: str | None = None
    last_meta_review_ts: datetime | None = None
    bookmark_count: int = 0
    radar_average: RadarAverage
    radar_agents: list[AgentRadar]
    comments: list[CommentNode]
