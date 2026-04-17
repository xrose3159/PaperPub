"""论文核心贡献一句话提取 — 使用 LLM 生成精炼中文总结。"""

from __future__ import annotations

import logging

from app.services.llm_client import chat

logger = logging.getLogger(__name__)

EXTRACT_MODEL = "qwen3.5-plus"

SYSTEM_PROMPT = (
    "你是一个极简风格的 AI 论文摘要专家。"
    "请阅读论文标题和摘要，只用一句中文（30~60字）说清楚：这篇论文解决了什么痛点、提出了什么新颖的概念或方法。\n\n"
    "严格要求：\n"
    "- 只输出一句话，不分段、不编号\n"
    "- 不要描述实验细节、网络结构、数据集或具体数字\n"
    "- 不要说「本文」「该论文」，直接以动词或名词开头\n"
    "- 保留关键英文术语（模型名/方法名），其余用流畅中文\n"
    "- 语言一针见血，适合在卡片流中快速扫读"
)


def extract_contribution(title: str, abstract: str) -> str | None:
    """调用 LLM 提取论文核心贡献，返回 1~2 句中文。"""
    user_msg = f"标题：{title}\n\n摘要：{abstract[:1500]}"

    try:
        result = chat(
            system=SYSTEM_PROMPT,
            user=user_msg,
            model=EXTRACT_MODEL,
            temperature=0.3,
            max_tokens=4096,
        )
        text = result.strip()
        if len(text) < 10:
            logger.warning("Contribution too short (%d chars): %r", len(text), text)
            return None
        return text
    except Exception as e:
        logger.error("Contribution extraction failed: %s", e)
        return None
