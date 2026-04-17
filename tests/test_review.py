"""AI Agent 评审流程测试脚本。

从数据库中取 1 篇论文，走完「3 Agent 打分 → 评论 → 盖楼互怼」全流程。

用法:
    # 先设置 API Key（三选一）：
    export OPENAI_API_KEY="sk-..."
    # 如果用兼容 API（如 DeepSeek / 智谱 / Moonshot），同时设置：
    export OPENAI_BASE_URL="https://api.deepseek.com/v1"
    export OPENAI_MODEL="deepseek-chat"

    cd PaperPub
    python -m tests.test_review
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from app.database import SessionLocal, init_db
from app.models.paper import Paper
from app.services.ai_reviewer import ensure_agents, review_paper


def main():
    print("=" * 60)
    print("  PaperPub · AI Agent 评审测试")
    print("=" * 60)

    # 0) 检查 API Key
    if not OPENAI_API_KEY:
        print("\n❌ 未设置 OPENAI_API_KEY 环境变量！")
        print("   请运行:  export OPENAI_API_KEY='sk-...'")
        print("   如用兼容 API，还需设置 OPENAI_BASE_URL 和 OPENAI_MODEL")
        sys.exit(1)

    print(f"\n  模型: {OPENAI_MODEL}")
    print(f"  API:  {OPENAI_BASE_URL}")

    # 1) 初始化
    init_db()
    db = SessionLocal()

    try:
        # 2) 确保 Agent 存在
        agents = ensure_agents(db)
        print(f"\n[1/5] 已就绪 {len(agents)} 位 Agent:")
        for a in agents:
            print(f"  {a.avatar or '🤖'} {a.name} — {a.bio}")

        # 3) 取一篇论文
        paper = db.query(Paper).order_by(Paper.created_at.desc()).first()
        if not paper:
            print("\n❌ 数据库中没有论文，请先运行爬虫:")
            print("   python -m tests.test_crawler")
            sys.exit(1)

        print(f"\n[2/5] 选取论文:")
        print(f"  📄 {paper.title}")
        print(f"  🔗 {paper.arxiv_url}")
        authors = json.loads(paper.authors)
        print(f"  👤 {', '.join(authors[:3])}{'...' if len(authors) > 3 else ''}")

        # 4) 执行评审
        print(f"\n[3/5] 开始评审（3 Agent 打分 + 评论 + 盖楼）...")
        print("  ⏳ 正在调用大模型，请稍候...\n")

        result = review_paper(paper, db)

        # 5) 打印打分结果
        print("[4/5] 打分结果（雷达图数据）:\n")
        print(f"  {'维度':<16} ", end="")
        for a in agents:
            print(f"{a.name:<20}", end="")
        print()
        print("  " + "-" * 76)

        dims = [
            ("novelty", "创新性"),
            ("rigor", "严谨性"),
            ("applicability", "应用价值"),
            ("clarity", "清晰度"),
            ("significance", "重要性"),
            ("reproducibility", "可复现性"),
        ]
        for field, label in dims:
            print(f"  {label:<16} ", end="")
            for s in result["scores"]:
                val = getattr(s, field)
                bar = "█" * int(val) + "░" * (10 - int(val))
                print(f"{bar} {val:<8.0f}", end="")
            print()

        print("  " + "-" * 76)
        print(f"  {'综合':<16} ", end="")
        for s in result["scores"]:
            print(f"{'⭐':>11} {s.overall:<8.1f}", end="")
        print()

        # 6) 打印评论 & 回复
        print(f"\n[5/5] 评论区:\n")
        for comment in result["comments"]:
            agent = next(a for a in agents if a.id == comment.agent_id)
            print(f"  {agent.avatar or '🤖'} {agent.name}:")
            for line in comment.content.split("\n"):
                print(f"    {line}")
            print()

            for reply in result["replies"]:
                if reply.parent_id == comment.id:
                    reply_agent = next(a for a in agents if a.id == reply.agent_id)
                    print(f"    ↳ {reply_agent.avatar or '🤖'} {reply_agent.name} 回复:")
                    for line in reply.content.split("\n"):
                        print(f"      {line}")
                    print()

        print("=" * 60)
        print("  ✅ 评审测试完成！")
        print("=" * 60)

    finally:
        db.close()


if __name__ == "__main__":
    main()
