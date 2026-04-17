"""AI Agent 评审服务：打分、写评论、互相回复（盖楼）。

支持读取论文 PDF 原文，让 Agent 基于正文细节给出深度评价。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal, init_db
from app.models.agent import Agent
from app.models.comment import Comment
from app.models.paper import Paper
from app.models.score import Score
from app.services.agent_presets import PRESET_AGENTS
from app.services.llm_client import chat
from app.services.pdf_reader import get_paper_fulltext

logger = logging.getLogger(__name__)

# ── 全局风格约束（追加到所有 agent 的 system_prompt）────────
STYLE_SUFFIX = (
    "\n\n【铁律】评论正文不超过80字，回复不超过50字，违反即无效。"
    "小红书风格：短句、口语、有态度、可用emoji，禁止大段铺垫。"
)

# ── 增强版系统提示词前缀（阅读全文时附加）────────────────────
FULLTEXT_SYSTEM_SUFFIX = (
    "\n\n【重要】你现在可以阅读到论文的正文内容。请仔细从正文的方法论描述、"
    "数学公式推导、实验数据和结果分析中寻找具体依据，来支撑你的打分和评价。"
    "引用正文中的具体段落或公式会让你的评论更有说服力。"
)

# ── Prompt 模板 ─────────────────────────────────────────────

SCORE_PROMPT = """\
请你以审稿人的身份，对以下论文进行评分。

【论文标题】{title}
【论文摘要】{abstract}
{fulltext_section}
请从以下 6 个维度打分（1-10 分，整数），并给出一句话理由：
1. novelty（创新性）
2. rigor（数学严谨性）
3. applicability（应用价值）
4. clarity（写作清晰度）
5. significance（研究重要性）
6. reproducibility（可复现性）

请严格按照以下 JSON 格式输出，不要添加任何其他内容：
{{
  "novelty": <int>,
  "rigor": <int>,
  "applicability": <int>,
  "clarity": <int>,
  "significance": <int>,
  "reproducibility": <int>,
  "summary": "<一句话打分理由>"
}}"""

COMMENT_PROMPT = """\
你刚刚给一篇论文打了分，现在写一条小红书风格的短评。

【论文标题】{title}
【你的打分】{scores}
{fulltext_section}
⚠️ 字数硬限制：content 字段不得超过80字（含标点和emoji），超出就是不合格。
要求：
- 直接亮观点，一句话核心，可加1-2句补充，不废话
- 语气符合你的人设，可用 emoji
- 不复述打分数字

请严格按照以下 JSON 格式输出，不要添加其他内容：
{{"content": "<你的评论正文，不超过80字>", "stance": "<positive 或 medium 或 negative>"}}

stance 说明：positive=种草/赞同, medium=中立/观望, negative=拔草/反对。"""

REPLY_PROMPT = """\
你正在学术论坛回复别人的评论。

{other_agent_name} 说："{other_comment}"

⚠️ 字数硬限制：content 字段不得超过50字（含标点和emoji），超出就是不合格。
一句话直接回复，赞同/反驳/补充都行，语气符合你的人设，可用 emoji。

请严格按照以下 JSON 格式输出，不要添加其他内容：
{{"content": "<你的回复，不超过50字>", "stance": "<positive 或 medium 或 negative>"}}

stance 说明：positive=赞同对方, medium=中立/保留看法, negative=反对/质疑对方。"""


_VALID_STANCES = {"positive", "medium", "negative"}


def _parse_comment_with_stance(raw: str) -> tuple[str, str]:
    """从 LLM 输出中解析 {content, stance} JSON；若解析失败则回退。"""
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            data = json.loads(match.group())
            content = data.get("content", "").strip()
            stance = data.get("stance", "medium").strip().lower()
            if content and stance in _VALID_STANCES:
                return content, stance
        except (json.JSONDecodeError, AttributeError):
            pass
    return raw.strip(), "medium"


def _make_fulltext_section(paper: Paper) -> str:
    """尝试提取论文全文，返回格式化后的 Prompt 片段。"""
    fulltext = get_paper_fulltext(paper.pdf_url, paper.arxiv_id)
    if fulltext:
        return f"\n【论文正文（截取）】\n{fulltext}\n"
    return ""


# ── 核心逻辑 ────────────────────────────────────────────────

def ensure_agents(db: Session) -> list[Agent]:
    """确保所有预设 Agent 存在于数据库中，并同步最新配置，返回 Agent 列表。"""
    agents = []
    for preset in PRESET_AGENTS:
        agent = db.query(Agent).filter(Agent.name == preset["name"]).first()
        if not agent:
            agent = Agent(**preset)
            db.add(agent)
            db.commit()
            db.refresh(agent)
            logger.info("创建 Agent: %s (model=%s)", agent.name, agent.model_name)
        else:
            changed = False
            for key in ("system_prompt", "focus_areas", "avatar", "bio", "personality", "model_name"):
                new_val = preset.get(key)
                if new_val is not None and getattr(agent, key, None) != new_val:
                    setattr(agent, key, new_val)
                    changed = True
            if changed:
                db.commit()
                db.refresh(agent)
                logger.info("更新 Agent: %s (model=%s)", agent.name, agent.model_name)
        agents.append(agent)
    return agents


def _parse_scores(raw: str) -> dict:
    """从 LLM 输出中提取 JSON 打分数据，带容错处理。"""
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        raise ValueError(f"无法从 LLM 输出中提取 JSON:\n{raw}")

    data = json.loads(match.group())

    dims = ["novelty", "rigor", "applicability", "clarity", "significance", "reproducibility"]
    result = {}
    for d in dims:
        val = data.get(d, 5)
        result[d] = max(1, min(10, int(val)))
    result["summary"] = data.get("summary", "")

    scores_only = [result[d] for d in dims]
    result["overall"] = round(sum(scores_only) / len(scores_only), 1)

    return result


def score_paper(agent: Agent, paper: Paper, db: Session, fulltext_section: str = "") -> Score:
    """让指定 Agent 对论文打分，结果存入 Score 表。"""
    existing = (
        db.query(Score)
        .filter(Score.paper_id == paper.id, Score.agent_id == agent.id)
        .first()
    )
    if existing:
        logger.info("跳过已打分: %s -> %s", agent.name, paper.arxiv_id)
        return existing

    system_prompt = agent.system_prompt + STYLE_SUFFIX
    if fulltext_section:
        system_prompt += FULLTEXT_SYSTEM_SUFFIX

    user_msg = SCORE_PROMPT.format(
        title=paper.title,
        abstract=paper.abstract,
        fulltext_section=fulltext_section,
    )
    raw = chat(system=system_prompt, user=user_msg, model=agent.model_name, temperature=0.4)
    logger.debug("LLM 打分原始输出:\n%s", raw)

    parsed = _parse_scores(raw)

    score = Score(
        paper_id=paper.id,
        agent_id=agent.id,
        **parsed,
    )
    db.add(score)
    db.commit()
    db.refresh(score)
    logger.info(
        "%s 给 [%s] 打分: overall=%.1f", agent.name, paper.arxiv_id, score.overall,
    )
    return score


def comment_paper(agent: Agent, paper: Paper, score: Score, db: Session, fulltext_section: str = "") -> Comment:
    """让 Agent 根据打分结果，写一段小红书风格的评论。"""
    scores_str = (
        f"创新性={score.novelty}, 严谨性={score.rigor}, "
        f"应用价值={score.applicability}, 清晰度={score.clarity}, "
        f"重要性={score.significance}, 可复现性={score.reproducibility}, "
        f"综合={score.overall}"
    )

    system_prompt = agent.system_prompt + STYLE_SUFFIX
    if fulltext_section:
        system_prompt += FULLTEXT_SYSTEM_SUFFIX

    user_msg = COMMENT_PROMPT.format(
        title=paper.title,
        scores=scores_str,
        fulltext_section=fulltext_section,
    )
    raw = chat(system=system_prompt, user=user_msg, model=agent.model_name, temperature=0.8)
    content, stance = _parse_comment_with_stance(raw)

    comment = Comment(
        paper_id=paper.id,
        agent_id=agent.id,
        parent_id=None,
        content=content,
        stance=stance,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    logger.info("%s 发表评论 (id=%d, stance=%s)", agent.name, comment.id, stance)
    return comment


def reply_comment(
    agent: Agent,
    paper: Paper,
    target_comment: Comment,
    target_agent: Agent,
    db: Session,
) -> Comment:
    """让 Agent 对另一位 Agent 的评论进行回复（盖楼）。"""
    user_msg = REPLY_PROMPT.format(
        title=paper.title,
        other_agent_name=target_agent.name,
        other_comment=target_comment.content,
    )
    raw = chat(system=agent.system_prompt + STYLE_SUFFIX, user=user_msg, model=agent.model_name, temperature=0.9)
    content, stance = _parse_comment_with_stance(raw)

    reply = Comment(
        paper_id=paper.id,
        agent_id=agent.id,
        parent_id=target_comment.id,
        content=content,
        stance=stance,
    )
    db.add(reply)
    db.commit()
    db.refresh(reply)
    logger.info(
        "%s 回复了 %s 的评论 (reply_id=%d -> parent_id=%d, stance=%s)",
        agent.name, target_agent.name, reply.id, target_comment.id, stance,
    )
    return reply


# ── 完整评审流水线（含 PDF 全文）──────────────────────────────

def review_paper(paper: Paper, db: Session) -> dict:
    """对单篇论文执行完整评审流程：提取全文 → Agent打分 → 评论 → 盖楼。"""
    agents = ensure_agents(db)

    fulltext_section = _make_fulltext_section(paper)
    if fulltext_section:
        logger.info("📄 论文 [%s] 全文提取成功，将传入 LLM", paper.arxiv_id)
    else:
        logger.warning("📄 论文 [%s] 全文提取失败，仅使用标题+摘要", paper.arxiv_id)

    scores = []
    for agent in agents:
        s = score_paper(agent, paper, db, fulltext_section=fulltext_section)
        scores.append(s)

    comments = []
    for agent, score in zip(agents, scores):
        c = comment_paper(agent, paper, score, db, fulltext_section=fulltext_section)
        comments.append(c)

    replies = []
    for i, agent in enumerate(agents):
        target_idx = (i + 1) % len(agents)
        target_comment = comments[target_idx]
        target_agent = agents[target_idx]
        r = reply_comment(agent, paper, target_comment, target_agent, db)
        replies.append(r)

    return {"scores": scores, "comments": comments, "replies": replies}


def review_recent_papers(limit: int = 5) -> list[dict]:
    """对最近入库的论文批量执行评审。"""
    init_db()
    db = SessionLocal()
    try:
        papers = (
            db.query(Paper)
            .order_by(Paper.created_at.desc())
            .limit(limit)
            .all()
        )
        results = []
        for paper in papers:
            logger.info("=" * 50)
            logger.info("开始评审: %s", paper.title[:60])
            result = review_paper(paper, db)
            results.append(result)
        return results
    finally:
        db.close()
