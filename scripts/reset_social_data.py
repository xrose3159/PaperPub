"""清理社交数据，保留论文。

删除顺序（按外键依赖）：
  Comment（自引用 parent_id → comments.id, 引用 agents.id / papers.id）
  → Score（引用 agents.id / papers.id）
  → Agent（引用 users.id）
  → User
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models.comment import Comment
from app.models.paper import Paper
from app.models.score import Score
from app.models.agent import Agent
from app.models.user import User


def reset():
    db = SessionLocal()

    n_comments = db.query(Comment).count()
    n_scores = db.query(Score).count()
    n_agents = db.query(Agent).count()
    n_users = db.query(User).count()
    n_papers = db.query(Paper).count()

    db.execute(Comment.__table__.delete())
    db.execute(Score.__table__.delete())
    db.execute(Agent.__table__.delete())
    db.execute(User.__table__.delete())

    db.commit()

    remaining_papers = db.query(Paper).count()
    db.close()

    print("=" * 50)
    print("  PaperPub 社交数据清理完成")
    print("=" * 50)
    print(f"  删除评论:   {n_comments} 条")
    print(f"  删除打分:   {n_scores} 条")
    print(f"  删除 Agent: {n_agents} 个")
    print(f"  删除用户:   {n_users} 个")
    print(f"  保留论文:   {remaining_papers} 篇 ✓")
    print("=" * 50)


if __name__ == "__main__":
    confirm = input("⚠️  即将清空所有评论、打分、Agent 和用户数据（论文保留）。确认？[y/N] ")
    if confirm.strip().lower() == "y":
        reset()
    else:
        print("已取消。")
