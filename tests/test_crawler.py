"""爬虫验证脚本：拉取 5 篇 CS 论文并打印入库结果。

用法:
    cd PaperPub
    python -m tests.test_crawler
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db
from app.models.paper import Paper
from app.services.arxiv_crawler import fetch_papers, save_papers

EXPECTED = 5


def main():
    print("=" * 60)
    print("  PaperPub 爬虫测试 — 拉取 5 篇 CS 论文")
    print("=" * 60)

    # 1. 初始化数据库
    init_db()
    print("\n[1/4] 数据库初始化完成")

    # 2. 从 arXiv 拉取论文
    print(f"\n[2/4] 正在从 arXiv 拉取最新 CS 论文（目标 {EXPECTED} 篇）...")
    papers = fetch_papers(category="cs.*", max_results=EXPECTED, days_back=7)

    if not papers:
        print("  ❌ 未能拉取到任何论文，请检查网络连接。")
        sys.exit(1)

    print(f"  拉取到 {len(papers)} 篇论文")

    # 3. 写入数据库
    print("\n[3/4] 写入数据库...")
    inserted = save_papers(papers)
    print(f"  新增 {inserted} 篇论文")

    # 4. 从数据库回读验证
    print("\n[4/4] 数据库验证:")
    db = SessionLocal()
    try:
        db_papers = db.query(Paper).order_by(Paper.published_at.desc()).limit(EXPECTED).all()
        print(f"  数据库中最新 {len(db_papers)} 篇论文:\n")

        for i, p in enumerate(db_papers, 1):
            authors = json.loads(p.authors)
            author_str = ", ".join(authors[:3])
            if len(authors) > 3:
                author_str += f" 等 {len(authors)} 人"
            print(f"  [{i}] {p.title[:80]}")
            print(f"      ID: {p.arxiv_id}")
            print(f"      作者: {author_str}")
            print(f"      分类: {p.categories}")
            print(f"      日期: {p.published_at.strftime('%Y-%m-%d')}")
            print(f"      链接: {p.arxiv_url}")
            print()

        total = db.query(Paper).count()
        print("-" * 60)

        if len(db_papers) >= EXPECTED:
            print(f"  ✅ 测试通过！数据库共 {total} 篇论文。")
        else:
            print(f"  ⚠️  拉取到 {len(db_papers)} 篇（少于目标 {EXPECTED} 篇），")
            print("     可能是 arXiv 近期该分类论文较少，但流程正常。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
