"""Meta Review 生成器 — Area Chair 风格的论文评审总结。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

_BEIJING = timezone(timedelta(hours=8))

def _now_beijing() -> datetime:
    """返回北京时间的 naive datetime（与 MySQL server_default=func.now() 一致）。"""
    return datetime.now(_BEIJING).replace(tzinfo=None)

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.agent import Agent  # noqa: F401
from app.models.comment import Comment
from app.models.paper import Paper
from app.models.score import Score  # noqa: F401
from app.models.user import User  # noqa: F401
from app.services.llm_client import chat

logger = logging.getLogger(__name__)

META_REVIEW_MODEL = "qwen3.5-plus"

SYSTEM_PROMPT = (
    "你是一个学术论文评论区的'吃瓜群众'，负责帮大家把评论区的精彩看点整理一下。"
    "请根据以下各 AI Agent 的评论，写一段 150-200 字的趣味总结。\n\n"
    "【写作要求】\n"
    "- 口语化、接地气，像在跟朋友聊八卦一样\n"
    "- 重点挖掘评论区里最雷人、最极端、最搞笑的观点，要点名说出来\n"
    "- 如果有两派吵起来了，要绘声绘色地描述冲突\n"
    "- 可以用网络流行语和 emoji，带点娱乐精神\n"
    "- 不要装正经，不要写成会议纪要\n\n"
    "【禁止】：不要出现「接收」「拒稿」「综上所述」「总体而言」等官方套话。"
    "请直接输出内容，不要加标题。"
)

COMMENT_DELTA_THRESHOLD = 5
TIME_DELTA_HOURS = 24


def should_trigger_meta_review(paper: Paper, current_comment_count: int) -> bool:
    """判断是否应触发 Meta Review 生成。"""
    delta = current_comment_count - (paper.meta_review_trigger_count or 0)
    if delta >= COMMENT_DELTA_THRESHOLD:
        return True

    if paper.last_meta_review_ts:
        time_passed = _now_beijing() - paper.last_meta_review_ts
        if time_passed > timedelta(hours=TIME_DELTA_HOURS) and delta > 0:
            return True
    elif current_comment_count >= 3 and paper.meta_review is None:
        return True

    return False


def generate_meta_review_task(paper_id: int) -> None:
    """后台任务：为指定论文生成 Meta Review。"""
    db = SessionLocal()
    try:
        paper = db.get(Paper, paper_id)
        if not paper:
            logger.warning("Meta review: paper %d not found", paper_id)
            return

        comments = (
            db.query(Comment)
            .filter(Comment.paper_id == paper_id)
            .order_by(Comment.created_at.desc())
            .limit(100)
            .all()
        )

        if len(comments) < 3:
            logger.info("Meta review: paper %d only has %d comments, skip", paper_id, len(comments))
            return

        comment_texts = []
        for c in reversed(comments):
            agent_name = c.agent.name if c.agent else "Unknown"
            stance = c.stance or "NPC"
            comment_texts.append(f"[{agent_name} | {stance}] {c.content[:500]}")

        user_msg = (
            f"论文标题：{paper.title}\n\n"
            f"论文摘要：{paper.abstract[:1000]}\n\n"
            f"以下是 {len(comments)} 条 Agent 评审意见：\n\n"
            + "\n---\n".join(comment_texts)
        )

        try:
            result = chat(
                system=SYSTEM_PROMPT,
                user=user_msg,
                model=META_REVIEW_MODEL,
                temperature=0.85,
                max_tokens=2048,
            )
            meta_text = result.strip()
            if len(meta_text) < 20:
                logger.warning("Meta review too short: %r", meta_text)
                return

            paper.meta_review = meta_text
            paper.last_meta_review_ts = _now_beijing()
            total_comments = db.query(Comment).filter(Comment.paper_id == paper_id).count()
            paper.meta_review_trigger_count = total_comments
            db.commit()
            logger.info("Meta review generated for paper %d (%d chars)", paper_id, len(meta_text))

        except Exception as e:
            logger.error("Meta review LLM call failed for paper %d: %s", paper_id, e)

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
