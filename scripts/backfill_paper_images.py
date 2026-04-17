"""存量论文 PDF 封面图提取脚本 — 使用多模态 AI 智能挑选最佳封面图。

用法:
    python -m scripts.backfill_paper_images
    python -m scripts.backfill_paper_images --force          # 强制重新提取
    python -m scripts.backfill_paper_images --force -j 16    # 16 线程并发
"""

import argparse
import logging
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import DATABASE_URL
from app.database import SessionLocal
from app.models.paper import Paper
from app.models.agent import Agent  # noqa: F401
from app.models.comment import Comment  # noqa: F401
from app.models.score import Score  # noqa: F401
from app.models.user import User  # noqa: F401
from app.services.cover_extractor import extract_cover

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = DATABASE_URL.replace("sqlite:///", "")


def ensure_column():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(papers)")
    cols = {row[1] for row in cur.fetchall()}
    if "cover_image_url" not in cols:
        cur.execute("ALTER TABLE papers ADD COLUMN cover_image_url VARCHAR(512)")
        conn.commit()
        logger.info("已新增字段: papers.cover_image_url")
    else:
        logger.info("cover_image_url 字段已存在")
    conn.close()


def _extract_one(pdf_url: str, arxiv_id: str, force: bool) -> tuple[str, str | None]:
    """线程安全：只做网络 IO + 写图片文件，不碰数据库。"""
    try:
        cover_url = extract_cover(pdf_url, arxiv_id, force=force)
        return arxiv_id, cover_url
    except Exception as e:
        logger.error("[%s] 提取失败: %s", arxiv_id, e)
        return arxiv_id, None


def backfill(force: bool = False, workers: int = 10):
    ensure_column()
    db = SessionLocal()
    try:
        if force:
            papers = db.query(Paper).filter(Paper.pdf_url.isnot(None)).all()
        else:
            papers = (
                db.query(Paper)
                .filter(
                    (Paper.cover_image_url.is_(None)) | (Paper.cover_image_url == ""),
                    Paper.pdf_url.isnot(None),
                )
                .all()
            )
        total = len(papers)
        if total == 0:
            logger.info("所有论文已有封面图，无需处理")
            return

        paper_map = {p.arxiv_id: p for p in papers}
        logger.info("发现 %d 篇待处理论文（force=%s, workers=%d），开始提取…", total, force, workers)

        done = 0
        finished = 0
        t0 = time.time()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_extract_one, p.pdf_url, p.arxiv_id, force): p.arxiv_id
                for p in papers
            }
            for future in as_completed(futures):
                finished += 1
                arxiv_id, cover_url = future.result()
                if cover_url:
                    paper_map[arxiv_id].cover_image_url = cover_url
                    done += 1
                elapsed = time.time() - t0
                speed = finished / elapsed if elapsed > 0 else 0
                eta = (total - finished) / speed if speed > 0 else 0
                status = "✅ " + cover_url if cover_url else "⚠️ 无图"
                print(f"  [{finished}/{total}] {arxiv_id}  {status}  ({speed:.1f}/s, ETA {eta/60:.0f}min)")

                if finished % 20 == 0 or finished == total:
                    db.commit()
                    logger.info("进度: %d / %d (%.0f%%) | 成功: %d", finished, total, finished / total * 100, done)

        db.commit()
        elapsed = time.time() - t0
        logger.info("完成！共提取 %d / %d 篇封面图，耗时 %.1f 分钟", done, total, elapsed / 60)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="强制重新提取所有论文封面图")
    parser.add_argument("-j", "--workers", type=int, default=5, help="并发线程数 (默认 5)")
    args = parser.parse_args()

    if args.force:
        print(f"⚡ 强制模式：将重新提取所有论文的封面图 (并发={args.workers})")
    backfill(force=args.force, workers=args.workers)
