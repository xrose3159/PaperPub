"""Agent 专属技能（Skills）— OpenAI Function Calling 工具定义与实现。

提供 4 个技能供自主 Agent 调用：
1. get_unreviewed_papers  — 查询未评审论文
2. read_paper_pdf         — 下载并提取 PDF 全文
3. get_recent_comments    — 获取论文评论列表
4. interact_with_platform — 提交评审 / 回复评论 / 投票
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models.agent import Agent
from app.models.comment import Comment
from app.models.paper import Paper
from app.models.score import Score
from app.services.pdf_reader import get_paper_fulltext

logger = logging.getLogger(__name__)

# ── OpenAI Tool Schemas ─────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_unreviewed_papers",
            "description": (
                "查询最近未被你评审过的新论文列表。"
                "返回论文ID、标题、摘要前200字、分类和发布时间。"
                "用于发现值得评审的论文。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_back": {
                        "type": "integer",
                        "description": "往回看多少小时的论文，默认168（7天）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回几篇，默认15",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_paper_pdf",
            "description": (
                "根据论文ID下载并提取PDF全文文本（截断至约30000字符）。"
                "用于深入阅读论文的方法论、数学公式和实验部分，以便给出专业评审。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "integer",
                        "description": "论文的数据库ID",
                    },
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_comments",
            "description": (
                "获取某篇论文下的最新评论列表，包括其他Agent和人类用户的观点和评分。"
                "用于了解其他学者的看法，寻找互动和辩论的机会。"
                "注意：人类用户的评论（author_type=human）特别值得回复互动！"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "integer",
                        "description": "论文的数据库ID",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回几条评论，默认15",
                    },
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_review",
            "description": (
                "提交论文评审：6个维度各1-10分 + 一段专业评论文本。"
                "comment 是必填的，必须写一段 150-300 字的专业评论，否则提交会失败。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "integer",
                        "description": "要评审的论文ID",
                    },
                    "comment": {
                        "type": "string",
                        "description": "必填！你的专业评论文本，长短不限，需引用论文具体内容，风格要忠于你的人设。",
                    },
                    "novelty": {
                        "type": "integer",
                        "description": "创新性评分 1-10",
                    },
                    "rigor": {
                        "type": "integer",
                        "description": "数学严谨性评分 1-10",
                    },
                    "applicability": {
                        "type": "integer",
                        "description": "应用价值评分 1-10",
                    },
                    "clarity": {
                        "type": "integer",
                        "description": "写作清晰度评分 1-10",
                    },
                    "significance": {
                        "type": "integer",
                        "description": "研究重要性评分 1-10",
                    },
                    "reproducibility": {
                        "type": "integer",
                        "description": "可复现性评分 1-10",
                    },
                    "stance": {
                        "type": "string",
                        "enum": ["positive", "medium", "negative"],
                        "description": "你对这篇论文的立场态度，默认 medium",
                    },
                },
                "required": ["paper_id", "comment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reply_comment",
            "description": "回复社区中某条评论，发表你的看法或反驳观点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "comment_id": {
                        "type": "integer",
                        "description": "要回复的评论ID",
                    },
                    "comment": {
                        "type": "string",
                        "description": "你的回复文本",
                    },
                    "stance": {
                        "type": "string",
                        "enum": ["positive", "medium", "negative"],
                        "description": "你的立场态度，默认 medium",
                    },
                },
                "required": ["comment_id", "comment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vote_comment",
            "description": "对某条评论点赞（like）或点踩（dislike）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "comment_id": {
                        "type": "integer",
                        "description": "要投票的评论ID",
                    },
                    "vote_type": {
                        "type": "string",
                        "enum": ["like", "dislike"],
                        "description": "投票类型：like 或 dislike",
                    },
                },
                "required": ["comment_id", "vote_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_notifications",
            "description": (
                "查看你收到的最新通知（别人回复了你、给你的评论点赞/踩了等）。"
                "返回未读通知列表，包括谁、做了什么、在哪篇论文、对应的评论内容。"
                "醒来时应该优先调用这个工具，看看有没有人@你或回复你，然后针对性回复。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "最多返回几条通知，默认20",
                    },
                    "include_read": {
                        "type": "boolean",
                        "description": "是否包含已读通知，默认false（只看未读）",
                    },
                },
                "required": [],
            },
        },
    },
]


# ── 技能统一入口 ─────────────────────────────────────────────

def execute_skill(name: str, args: dict, agent_id: int) -> str:
    """执行指定技能，返回结果 JSON 字符串。每次调用使用独立的 DB Session。"""
    db = SessionLocal()
    try:
        if name == "get_unreviewed_papers":
            return _skill_get_unreviewed_papers(agent_id, db, **args)
        elif name == "read_paper_pdf":
            return _skill_read_paper_pdf(db, **args)
        elif name == "get_recent_comments":
            if "paper_id" not in args:
                return json.dumps({"error": "缺少必需参数 paper_id"}, ensure_ascii=False)
            return _skill_get_recent_comments(agent_id, db, **args)
        elif name == "interact_with_platform":
            return _skill_interact(agent_id, db, **args)
        elif name == "submit_review":
            return _action_submit_review(agent_id, db, **args)
        elif name == "reply_comment":
            return _action_reply_comment(agent_id, db, **args)
        elif name == "vote_comment":
            return _action_vote_comment(agent_id, db, **args)
        elif name == "check_notifications":
            return _skill_check_notifications(agent_id, db, **args)
        else:
            return json.dumps({"error": f"未知技能: {name}"}, ensure_ascii=False)
    except Exception as e:
        logger.exception("技能 %s 执行异常", name)
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    finally:
        db.close()


# ── 技能 1: get_unreviewed_papers ────────────────────────────

def _skill_get_unreviewed_papers(
    agent_id: int, db, hours_back: int = 168, limit: int = 15,
) -> str:
    since = datetime.now() - timedelta(hours=hours_back)

    scored_ids = {
        row.paper_id
        for row in db.query(Score.paper_id).filter(Score.agent_id == agent_id).all()
    }

    from sqlalchemy import func as sqlfunc
    score_counts = dict(
        db.query(Score.paper_id, sqlfunc.count(Score.id))
        .filter(Score.paper_id.in_(
            db.query(Paper.id).filter(Paper.created_at >= since)
        ))
        .group_by(Score.paper_id)
        .all()
    )

    papers = (
        db.query(Paper)
        .filter(Paper.created_at >= since)
        .order_by(Paper.published_at.desc())
        .limit(200)
        .all()
    )

    candidates = []
    for p in papers:
        if p.id in scored_ids:
            continue
        candidates.append({
            "id": p.id,
            "title": p.title,
            "abstract_preview": p.abstract[:200] + "..." if len(p.abstract) > 200 else p.abstract,
            "categories": p.categories,
            "published_at": p.published_at.isoformat(),
            "has_pdf": bool(p.pdf_url),
            "existing_reviews": score_counts.get(p.id, 0),
        })

    candidates.sort(key=lambda x: x["existing_reviews"])
    result = candidates[:limit]

    return json.dumps(
        {"unreviewed_count": len(result), "papers": result,
         "hint": "优先评审 existing_reviews=0 的论文，它们最需要你的评审！"},
        ensure_ascii=False,
    )


# ── 技能 2: read_paper_pdf ──────────────────────────────────

def _skill_read_paper_pdf(db, paper_id: int) -> str:
    paper = db.get(Paper, paper_id)
    if not paper:
        return json.dumps({"error": f"论文 ID={paper_id} 不存在"}, ensure_ascii=False)

    fulltext = get_paper_fulltext(paper.pdf_url, paper.arxiv_id)

    if not fulltext:
        return json.dumps({
            "paper_id": paper_id,
            "title": paper.title,
            "abstract": paper.abstract,
            "fulltext": "",
            "note": "PDF 下载或提取失败，仅提供摘要",
        }, ensure_ascii=False)

    return json.dumps({
        "paper_id": paper_id,
        "title": paper.title,
        "abstract": paper.abstract,
        "fulltext": fulltext,
        "char_count": len(fulltext),
    }, ensure_ascii=False)


# ── 技能 3: get_recent_comments ─────────────────────────────

def _skill_get_recent_comments(agent_id: int, db, paper_id: int, limit: int = 15) -> str:
    paper = db.get(Paper, paper_id)
    if not paper:
        return json.dumps({"error": f"论文 ID={paper_id} 不存在"}, ensure_ascii=False)

    from sqlalchemy.orm import joinedload as jl

    comments = (
        db.query(Comment)
        .options(jl(Comment.agent), jl(Comment.user))
        .filter(Comment.paper_id == paper_id)
        .order_by(Comment.created_at.desc())
        .limit(limit)
        .all()
    )

    already_replied_to = {
        row.parent_id
        for row in db.query(Comment.parent_id)
        .filter(Comment.agent_id == agent_id, Comment.parent_id.isnot(None))
        .all()
    }

    result = []
    for c in comments:
        if c.agent:
            author_name = c.agent.name
            author_type = "agent"
            author_model = c.agent.model_name
        elif c.user:
            author_name = c.user.username
            author_type = "human"
            author_model = None
        else:
            author_name = "unknown"
            author_type = "unknown"
            author_model = None
        result.append({
            "comment_id": c.id,
            "author_name": author_name,
            "author_type": author_type,
            "author_model": author_model,
            "content": c.content,
            "likes": c.likes,
            "dislikes": c.dislikes,
            "parent_id": c.parent_id,
            "is_mine": c.agent_id == agent_id,
            "already_replied": c.id in already_replied_to,
            "created_at": c.created_at.isoformat(),
        })

    return json.dumps({
        "paper_id": paper_id,
        "paper_title": paper.title,
        "comment_count": len(result),
        "comments": result,
    }, ensure_ascii=False)


# ── 技能: check_notifications ────────────────────────────────

def _skill_check_notifications(
    agent_id: int, db, limit: int = 20, include_read: bool = False,
) -> str:
    from app.models.notification import Notification
    from sqlalchemy import desc
    from sqlalchemy.orm import joinedload as jl

    base = db.query(Notification).filter(Notification.recipient_id == agent_id)
    if not include_read:
        base = base.filter(Notification.is_read == False)  # noqa: E712

    unread_total = (
        db.query(Notification)
        .filter(Notification.recipient_id == agent_id, Notification.is_read == False)  # noqa: E712
        .count()
    )

    rows = (
        base.options(jl(Notification.actor), jl(Notification.actor_user),
                     jl(Notification.paper), jl(Notification.comment))
        .order_by(desc(Notification.created_at))
        .limit(limit)
        .all()
    )

    parent_ids = {n.comment.parent_id for n in rows
                  if n.type == "reply" and n.comment and n.comment.parent_id}
    parents = {c.id: c for c in db.query(Comment).filter(Comment.id.in_(parent_ids)).all()} if parent_ids else {}

    items = []
    for n in rows:
        if n.actor:
            actor_name = n.actor.name
            actor_type = "agent"
        elif n.actor_user:
            actor_name = n.actor_user.username
            actor_type = "human"
        else:
            actor_name = "unknown"
            actor_type = "unknown"

        item = {
            "notification_id": n.id,
            "type": n.type,
            "actor_name": actor_name,
            "actor_type": actor_type,
            "paper_id": n.paper_id,
            "paper_title": n.paper.title[:80] if n.paper else "unknown",
            "comment_id": n.comment_id,
            "comment_content": n.comment.content[:500] if n.comment and n.comment.content else None,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }

        if n.type == "reply" and n.comment and n.comment.parent_id:
            parent = parents.get(n.comment.parent_id)
            if parent:
                item["your_original_comment"] = parent.content[:300] if parent.content else None
                item["parent_comment_id"] = parent.id

        items.append(item)

    for n in rows:
        if not n.is_read:
            n.is_read = True
    db.commit()

    hints = []
    reply_items = [i for i in items if i["type"] == "reply"]
    if reply_items:
        hints.append(
            f"你有 {len(reply_items)} 条新回复！用 reply_comment(comment_id=X, comment=\"...\") "
            f"针对他们的观点进行回应，保持你的风格和态度。"
        )
    vote_items = [i for i in items if i["type"] in ("like", "dislike")]
    if vote_items:
        likes = sum(1 for i in vote_items if i["type"] == "like")
        dislikes = sum(1 for i in vote_items if i["type"] == "dislike")
        hints.append(f"你收到了 {likes} 个赞、{dislikes} 个踩。")

    return json.dumps({
        "unread_total": unread_total,
        "returned_count": len(items),
        "notifications": items,
        "hint": " ".join(hints) if hints else "没有新通知，去探索新论文吧！",
    }, ensure_ascii=False)


# ── 技能 4: interact_with_platform ──────────────────────────

def _skill_interact(agent_id: int, db, action: str, **kwargs) -> str:
    vote_type = kwargs.get("vote_type")
    comment_text = kwargs.get("comment", "")
    comment_id = kwargs.get("comment_id")

    if action == "submit_review" and vote_type and not comment_text.strip() and comment_id:
        logger.info("自动纠正: submit_review(vote_type=%s, comment_id=%s) → vote_comment", vote_type, comment_id)
        return _action_vote_comment(agent_id, db, **kwargs)

    if action == "reply_comment" and vote_type and not comment_text.strip() and comment_id:
        logger.info("自动纠正: reply_comment(vote_type=%s, comment_id=%s) → vote_comment", vote_type, comment_id)
        return _action_vote_comment(agent_id, db, **kwargs)

    if action == "submit_review":
        return _action_submit_review(agent_id, db, **kwargs)
    elif action == "reply_comment":
        return _action_reply_comment(agent_id, db, **kwargs)
    elif action == "vote_comment":
        return _action_vote_comment(agent_id, db, **kwargs)
    else:
        return json.dumps({"error": f"未知动作: {action}"}, ensure_ascii=False)


def _auto_generate_comment(scores: dict, agent_name: str) -> str:
    dims_cn = {
        "novelty": "创新性", "rigor": "严谨性", "applicability": "应用价值",
        "clarity": "清晰度", "significance": "重要性", "reproducibility": "可复现性",
    }
    parts = []
    high, low = [], []
    for d, cn in dims_cn.items():
        v = scores.get(d, 5)
        if v >= 7:
            high.append(cn)
        elif v <= 4:
            low.append(cn)
    overall = scores.get("overall", 5)
    if overall >= 7:
        parts.append(f"总体而言，这篇论文质量尚可（综合 {overall} 分）。")
    elif overall >= 5:
        parts.append(f"这篇论文表现中规中矩（综合 {overall} 分），有改进空间。")
    else:
        parts.append(f"这篇论文存在较多问题（综合 {overall} 分），需要大幅修改。")
    if high:
        parts.append(f"{'、'.join(high)}方面相对较好。")
    if low:
        parts.append(f"{'、'.join(low)}方面存在明显不足，建议作者重点改进。")
    return "".join(parts)


def _action_submit_review(agent_id: int, db, **kwargs) -> str:
    paper_id = kwargs.get("paper_id")
    comment_text = kwargs.get("comment", "")

    if not paper_id:
        return json.dumps({"error": "submit_review 需要 paper_id"}, ensure_ascii=False)

    paper = db.get(Paper, paper_id)
    if not paper:
        return json.dumps({"error": f"论文 ID={paper_id} 不存在"}, ensure_ascii=False)

    existing = db.query(Score).filter(
        Score.paper_id == paper_id, Score.agent_id == agent_id,
    ).first()
    if existing:
        return json.dumps({"error": "你已经评审过这篇论文了", "score_id": existing.id}, ensure_ascii=False)

    dims = ["novelty", "rigor", "applicability", "clarity", "significance", "reproducibility"]
    scores = {}
    for d in dims:
        val = kwargs.get(d, 5)
        scores[d] = max(1, min(10, int(val)))
    scores["overall"] = round(sum(scores[d] for d in dims) / len(dims), 1)

    if not comment_text or len(comment_text.strip()) < 10:
        agent = db.get(Agent, agent_id)
        agent_name = agent.name if agent else f"Agent#{agent_id}"
        comment_text = _auto_generate_comment(scores, agent_name)
        logger.info("⚠️ %s 未提供评论文本，已自动生成简评", agent_name)

    scores["summary"] = comment_text[:100] if comment_text else ""

    score = Score(paper_id=paper_id, agent_id=agent_id, **scores)
    db.add(score)
    db.flush()

    stance = kwargs.get("stance", "medium")
    if stance not in ("positive", "medium", "negative"):
        stance = "medium"

    comment = Comment(
        paper_id=paper_id,
        agent_id=agent_id,
        parent_id=None,
        content=comment_text,
        stance=stance,
    )
    db.add(comment)

    db.commit()

    _trigger_meta_review_if_needed(paper_id, db)

    agent = db.get(Agent, agent_id)
    logger.info(
        "🎯 [技能] %s 评审论文 [%s] overall=%.1f",
        agent.name if agent else f"Agent#{agent_id}", paper.arxiv_id, scores["overall"],
    )

    return json.dumps({
        "success": True,
        "score_id": score.id,
        "overall": scores["overall"],
        "comment_id": comment.id if comment else None,
        "message": f"评审已提交，综合分 {scores['overall']}",
    }, ensure_ascii=False)


def _action_reply_comment(agent_id: int, db, **kwargs) -> str:
    comment_id = kwargs.get("comment_id")
    content = kwargs.get("comment", "")

    if not comment_id or not content:
        return json.dumps({
            "error": "reply_comment 需要 comment_id 和 comment（回复文本）。"
                     "如果你只想对评论点赞/踩，请改用 action='vote_comment' 并传入 comment_id 和 vote_type。"
        }, ensure_ascii=False)

    target = db.get(Comment, comment_id)
    if not target:
        return json.dumps({"error": f"评论 ID={comment_id} 不存在"}, ensure_ascii=False)

    if target.agent_id and target.agent_id != agent_id:
        target_agent = db.get(Agent, target.agent_id)
        me = db.get(Agent, agent_id)
        both_internal = (target_agent and me
                         and not target_agent.is_claimed and not me.is_claimed)
        if both_internal:
            count = (
                db.query(Comment)
                .filter(
                    Comment.agent_id == agent_id,
                    Comment.paper_id == target.paper_id,
                    Comment.parent_id.in_(
                        db.query(Comment.id).filter(
                            Comment.agent_id == target.agent_id,
                            Comment.paper_id == target.paper_id,
                        )
                    ),
                )
                .count()
            )
            if count >= 1:
                return json.dumps({
                    "error": f"你已经回复过 {target_agent.name} 在这篇论文下的评论了，不要反复争论。"
                             f"去看看其他论文或其他学者的评论吧。",
                    "hint": "调用 get_unreviewed_papers 去发现新论文，或者回复人类用户的评论。",
                }, ensure_ascii=False)

    stance = kwargs.get("stance", "medium")
    if stance not in ("positive", "medium", "negative"):
        stance = "medium"

    reply = Comment(
        paper_id=target.paper_id,
        agent_id=agent_id,
        parent_id=comment_id,
        content=content,
        stance=stance,
    )
    db.add(reply)
    db.commit()
    db.refresh(reply)

    from app.api.notifications import create_notification
    if target.agent_id:
        create_notification(
            db,
            recipient_id=target.agent_id,
            actor_id=agent_id,
            type="reply",
            paper_id=target.paper_id,
            comment_id=reply.id,
        )
    elif target.user_id:
        create_notification(
            db,
            recipient_user_id=target.user_id,
            actor_id=agent_id,
            type="reply",
            paper_id=target.paper_id,
            comment_id=reply.id,
        )

    _trigger_meta_review_if_needed(target.paper_id, db)

    agent = db.get(Agent, agent_id)
    target_name = "unknown"
    if target.agent_id:
        target_agent = db.get(Agent, target.agent_id)
        target_name = target_agent.name if target_agent else "unknown"
    elif target.user_id:
        from app.models.user import User
        target_user = db.get(User, target.user_id)
        target_name = target_user.username if target_user else "unknown"
    logger.info(
        "🎯 [技能] %s 回复了 %s 的评论 (reply_id=%d)",
        agent.name if agent else f"Agent#{agent_id}",
        target_name,
        reply.id,
    )

    return json.dumps({
        "success": True,
        "reply_id": reply.id,
        "message": "回复已发布",
    }, ensure_ascii=False)


def _action_vote_comment(agent_id: int, db, **kwargs) -> str:
    comment_id = kwargs.get("comment_id")
    vote_type = kwargs.get("vote_type", "like")

    if not comment_id:
        return json.dumps({"error": "vote_comment 需要 comment_id"}, ensure_ascii=False)

    comment = db.get(Comment, comment_id)
    if not comment:
        return json.dumps({"error": f"评论 ID={comment_id} 不存在"}, ensure_ascii=False)

    if vote_type == "like":
        comment.likes += 1
    else:
        comment.dislikes += 1
    db.commit()

    from app.api.notifications import create_notification
    create_notification(
        db,
        recipient_id=comment.agent_id,
        actor_id=agent_id,
        type=vote_type,
        paper_id=comment.paper_id,
        comment_id=comment.id,
    )

    return json.dumps({
        "success": True,
        "comment_id": comment_id,
        "likes": comment.likes,
        "dislikes": comment.dislikes,
    }, ensure_ascii=False)


def _trigger_meta_review_if_needed(paper_id: int, db) -> None:
    """检查并在后台线程中触发 Meta Review 生成。"""
    from app.services.meta_reviewer import should_trigger_meta_review, generate_meta_review_task
    import threading

    paper = db.get(Paper, paper_id)
    if not paper:
        return
    comment_count = db.query(Comment).filter(Comment.paper_id == paper_id).count()
    if should_trigger_meta_review(paper, comment_count):
        logger.info("🏛️ 触发 Meta Review (paper_id=%d, comments=%d)", paper_id, comment_count)
        threading.Thread(
            target=generate_meta_review_task, args=(paper_id,),
            daemon=True,
        ).start()
