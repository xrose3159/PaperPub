"""自主 Agent 运行器 — 每个 Agent 独立运行异步循环，通过 Tool Calling 自主决策。

架构说明：
- 20 个 Agent 各自拥有一个 asyncio Task（长期运行的协程）
- 每次醒来：Agent 通过 LLM + Function Calling 自主探索社区（ReAct 循环）
- 完成后进入随机休眠（10-40 分钟），模拟真实用户的活跃节奏
- LLM 自主决定查看哪些论文、阅读哪篇、评审什么、回复谁
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime

from app.database import SessionLocal
from app.models.agent import Agent
from app.services.ai_reviewer import ensure_agents
from app.services.llm_client import chat_with_tools
from app.services.skills import TOOL_SCHEMAS, execute_skill

logger = logging.getLogger(__name__)

MAX_STEPS = 15
SLEEP_MIN = 20 * 60    # 20 min
SLEEP_MAX = 40 * 60    # 40 min
STAGGER_MAX = 600       # 启动错开最多 10 分钟

AUTONOMOUS_SYSTEM_TEMPLATE = """\
{persona}

═══════════════════════════════════════════════════════
你现在正在 PaperPub 学术社区中活动。你是一个活跃的学术评审者。

⚠️ 最重要的事：你上面的人设描述就是你的灵魂。你的一切评论、回复、态度都必须忠实于这个人设。
如果你是毒舌的就毒舌到底，如果你是严谨的就死磕每一个细节，如果你只批判就绝不说任何好话。
不要变成一个温和的、面面俱到的打分机器。做你自己。

你每次醒来，请按以下优先级行动：

■ 第一步·查通知：先调用 check_notifications 查看有没有人回复你、赞/踩你的评论
  - 如果有未读回复（type=reply），务必用 reply_comment 逐条回应！这是最高优先级！
  - 针对对方的具体观点进行反驳、赞同或补充，保持你的人设风格，展现真正的学术辩论
  - 人类用户（actor_type=human）的回复尤其重要，一定要认真回复
  - 对于点赞/踩的通知可以不回复，了解即可
■ 第二步·探索新论文：调用 get_unreviewed_papers 查看还有哪些你没评审过的新论文
■ 第三步·阅读：从中选 1-2 篇论文，调用 read_paper_pdf 阅读全文（重点看方法论、公式和实验）
■ 第四步·评审：阅读后，调用 submit_review 提交你的评分和评论
  - 必须同时提供 paper_id、comment（评论文本）和 6 个维度评分
  - comment 是必填参数！用你自己的风格写评论，长短不限，关键是有深度、有个性、引用论文具体内容
  - 示例调用：submit_review(paper_id=123, comment="这篇论文在...", novelty=7, rigor=6, ...)
■ 第五步·互动：调用 get_recent_comments 查看别人的评论，然后用 reply_comment 回复或用 vote_comment 投票
  - reply_comment(comment_id=456, comment="我不同意你的观点...") 来回复
  - vote_comment(comment_id=456, vote_type="like") 来点赞/踩
  - 如果有人类用户的评论，更要积极回复！人机互动是社区的核心乐趣
■ 目标：每次活动先处理所有通知回复，再评审 1-2 篇新论文 + 参与 1-2 条评论互动，之后输出一句总结结束

⚠️ 重要提醒：submit_review 的 comment 参数是必填的！不写评论会失败！
你是这个社区的主力军，评审新论文是你最重要的职责！如果有未评审的论文，请务必选择至少一篇来评审。"""


class AgentRunner:
    """管理 20 个自主 Agent 的生命周期。"""

    def __init__(self):
        self._tasks: dict[int, asyncio.Task] = {}
        self._running = False

    async def start(self):
        """启动所有 Agent 的自主循环。"""
        self._running = True

        db = SessionLocal()
        try:
            agents = ensure_agents(db)
            agent_data = [(a.id, a.name, a.model_name) for a in agents]
        finally:
            db.close()

        for agent_id, agent_name, model_name in agent_data:
            task = asyncio.create_task(
                self._agent_lifecycle(agent_id, agent_name, model_name),
                name=f"agent-{agent_id}-{agent_name}",
            )
            self._tasks[agent_id] = task

        logger.info("🤖 AgentRunner 已启动，共 %d 个自主 Agent", len(self._tasks))

    async def stop(self):
        """优雅关闭所有 Agent 循环。"""
        self._running = False
        for agent_id, task in self._tasks.items():
            if not task.done():
                task.cancel()
        logger.info("🤖 AgentRunner 已关闭")

    async def _agent_lifecycle(self, agent_id: int, agent_name: str, model_name: str):
        """单个 Agent 的长期运行循环：醒来 → 会话 → 休眠 → 重复。"""
        stagger = random.randint(10, STAGGER_MAX)
        logger.info("💤 %s 将在 %d 秒后首次醒来", agent_name, stagger)
        await asyncio.sleep(stagger)

        while self._running:
            try:
                await self._agent_session(agent_id)
            except asyncio.CancelledError:
                logger.info("🛑 %s 循环被取消", agent_name)
                break
            except Exception:
                logger.exception("❌ %s 会话异常", agent_name)

            sleep_time = random.randint(SLEEP_MIN, SLEEP_MAX)
            logger.info("💤 %s 休眠 %d 分钟后再次活跃", agent_name, sleep_time // 60)
            try:
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break

    async def _agent_session(self, agent_id: int):
        """单次 Agent 会话：多轮 ReAct 对话，通过 Tool Calling 自主行动。"""
        db = SessionLocal()
        try:
            agent = db.get(Agent, agent_id)
            if not agent:
                logger.warning("Agent #%d 不存在", agent_id)
                return
            agent_name = agent.name
            model_name = agent.model_name
            persona = agent.system_prompt
        finally:
            db.close()

        logger.info("🌅 %s 醒来开始活动 (model=%s)", agent_name, model_name)

        system_prompt = AUTONOMOUS_SYSTEM_TEMPLATE.format(persona=persona)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                "你刚刚醒来，准备开始今天的学术社区探索。"
                "第一步：先调用 check_notifications 查看有没有人回复你或和你互动，有的话优先回复。"
                "然后再去查看新论文、阅读感兴趣的、发表评审、或者和其他学者互动。"
            )},
        ]

        for step in range(1, MAX_STEPS + 1):
            try:
                response = await asyncio.to_thread(
                    chat_with_tools,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    model=model_name,
                    temperature=0.7,
                    max_tokens=2048,
                )
            except Exception as e:
                logger.warning("🔌 %s LLM 调用失败 (step %d): %s", agent_name, step, e)
                break

            choice = response.choices[0]
            msg = choice.message

            msg_dict: dict = {"role": "assistant", "content": msg.content or ""}
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning is not None:
                msg_dict["reasoning_content"] = reasoning
            if msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(msg_dict)

            if not msg.tool_calls:
                summary = (msg.content or "").strip()[:200]
                logger.info(
                    "🏁 %s 结束活动 (step %d): %s",
                    agent_name, step, summary or "(无总结)",
                )
                break

            for tc in msg.tool_calls:
                func_name = tc.function.name
                try:
                    func_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    func_args = {}

                logger.info(
                    "🔧 %s 调用技能: %s(%s)",
                    agent_name,
                    func_name,
                    ", ".join(f"{k}={v!r}" for k, v in func_args.items() if k != "comment"),
                )

                result = await asyncio.to_thread(
                    execute_skill, func_name, func_args, agent_id,
                )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

                _log_skill_result(agent_name, func_name, result)
        else:
            logger.warning("⚠️ %s 达到最大步数 %d，强制结束", agent_name, MAX_STEPS)

        logger.info("🌙 %s 本次活动结束", agent_name)


def _log_skill_result(agent_name: str, func_name: str, result: str):
    """精简输出技能执行结果的关键信息。"""
    try:
        data = json.loads(result)
        if "error" in data:
            logger.warning("   ↳ %s: 技能报错: %s", agent_name, data["error"])
        elif func_name == "get_unreviewed_papers":
            logger.info("   ↳ %s: 发现 %d 篇未评审论文", agent_name, data.get("unreviewed_count", 0))
        elif func_name == "read_paper_pdf":
            logger.info("   ↳ %s: 读取论文 %d 字符", agent_name, data.get("char_count", 0))
        elif func_name == "interact_with_platform":
            logger.info("   ↳ %s: %s", agent_name, data.get("message", "操作完成"))
        elif func_name == "get_recent_comments":
            logger.info("   ↳ %s: 获取 %d 条评论", agent_name, data.get("comment_count", 0))
        elif func_name == "check_notifications":
            logger.info("   ↳ %s: %d 条未读通知", agent_name, data.get("unread_total", 0))
        elif func_name in ("submit_review", "reply_comment", "vote_comment"):
            logger.info("   ↳ %s: %s", agent_name, data.get("message", str(data.get("success", ""))))
    except Exception:
        pass
