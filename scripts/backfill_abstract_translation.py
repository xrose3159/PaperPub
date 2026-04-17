"""存量论文摘要中文翻译脚本。

用法:
    conda run -n paper python -m scripts.backfill_abstract_translation
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
from app.services.abstract_translator import translate_abstract

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = DATABASE_URL.replace("sqlite:///", "")


def ensure_column():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(papers)")
    cols = {row[1] for row in cur.fetchall()}
    if "zh_abstract" not in cols:
        cur.execute("ALTER TABLE papers ADD COLUMN zh_abstract TEXT")
        conn.commit()
        logger.info("已新增字段: papers.zh_abstract")
    else:
        logger.info("zh_abstract 字段已存在")
    conn.close()


def backfill():
    ensure_column()
    db = SessionLocal()
    try:
        papers = (
            db.query(Paper)
            .filter((Paper.zh_abstract.is_(None)) | (Paper.zh_abstract == ""))
            .all()
        )
        total = len(papers)
        if total == 0:
            logger.info("所有论文已有中文摘要，无需处理")
            return

        logger.info("发现 %d 篇待翻译论文，开始处理…", total)
        done = 0
        for i, paper in enumerate(papers, 1):
            zh = translate_abstract(paper.abstract)
            if zh:
                paper.zh_abstract = zh
                done += 1
            print(f"  [{i}/{total}] {paper.arxiv_id}  {'✅' if zh else '⚠️ 跳过'}")

            if i % 10 == 0 or i == total:
                db.commit()
                logger.info("进度: %d / %d (%.0f%%)", i, total, i / total * 100)
            time.sleep(0.5)

        db.commit()
        logger.info("翻译完成！共翻译 %d / %d 篇", done, total)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    backfill()
