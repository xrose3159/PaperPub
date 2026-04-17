"""存量论文核心贡献补充脚本 — 为 core_contribution 为空的论文调用 LLM 生成一句话总结。

用法:
    conda run -n paper python -m scripts.backfill_contributions
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
from app.services.contribution_extractor import extract_contribution

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH = DATABASE_URL.replace("sqlite:///", "")


def ensure_column():
    """确保 papers 表有 core_contribution 列。"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(papers)")
    cols = {row[1] for row in cur.fetchall()}
    if "core_contribution" not in cols:
        cur.execute("ALTER TABLE papers ADD COLUMN core_contribution TEXT")
        conn.commit()
        logger.info("已新增字段: papers.core_contribution")
    conn.close()


def backfill():
    ensure_column()

    db = SessionLocal()
    try:
        papers = (
            db.query(Paper)
            .filter((Paper.core_contribution.is_(None)) | (Paper.core_contribution == ""))
            .all()
        )
        total = len(papers)
        if total == 0:
            logger.info("所有论文已有核心贡献总结，无需处理")
            return

        logger.info("发现 %d 篇论文需要生成核心贡献总结…", total)

        for i, paper in enumerate(papers, 1):
            contrib = extract_contribution(paper.title, paper.abstract)
            if contrib:
                paper.core_contribution = contrib
                print(f"  [{i}/{total}] {paper.arxiv_id}: {contrib[:60]}…")
            else:
                print(f"  [{i}/{total}] {paper.arxiv_id}: (生成失败，跳过)")

            if i % 5 == 0 or i == total:
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
