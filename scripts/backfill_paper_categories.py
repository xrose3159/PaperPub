"""存量论文 AI 多标签分类清洗脚本 — 对数据库中所有论文使用 LLM 重新打上 1~3 个标签。

用法:
    conda run -n paper python -m scripts.backfill_paper_categories
"""

import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import DATABASE_URL
from app.database import SessionLocal
from app.models.paper import Paper
from app.models.agent import Agent  # noqa: F401
from app.models.comment import Comment  # noqa: F401
from app.models.score import Score  # noqa: F401
from app.models.user import User  # noqa: F401
from app.services.paper_classifier import classify_paper_with_llm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH = DATABASE_URL.replace("sqlite:///", "")


def ensure_columns():
    """确保 papers 表有 ai_tags 列（JSON）和 ai_category 列。"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(papers)")
    cols = {row[1] for row in cur.fetchall()}
    if "ai_tags" not in cols:
        cur.execute("ALTER TABLE papers ADD COLUMN ai_tags TEXT")
        conn.commit()
        logger.info("已新增字段: papers.ai_tags")
    if "ai_category" not in cols:
        cur.execute("ALTER TABLE papers ADD COLUMN ai_category VARCHAR(64)")
        conn.commit()
        logger.info("已新增字段: papers.ai_category")
    conn.close()


def backfill():
    ensure_columns()

    db = SessionLocal()
    try:
        papers = db.query(Paper).all()
        total = len(papers)
        if total == 0:
            logger.info("数据库中没有论文")
            return

        logger.info("共 %d 篇论文，开始多标签分类…", total)

        for i, paper in enumerate(papers, 1):
            old_tags = paper.ai_tags or []
            tags = classify_paper_with_llm(paper.title, paper.abstract)
            paper.ai_tags = tags
            paper.ai_category = tags[0] if tags else "Core ML"
            print(f"  [{i}/{total}] {paper.arxiv_id}  {old_tags} -> {tags}")

            if i % 10 == 0 or i == total:
                db.commit()
                logger.info("进度: %d / %d (%.0f%%)", i, total, i / total * 100)

            time.sleep(0.5)

        db.commit()
        logger.info("全部完成！共处理 %d 篇论文", total)

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    backfill()
