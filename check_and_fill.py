"""检查两天内 arXiv CS 论文是否爬完，并补爬缺失的。"""
import arxiv
import json
import logging
import time
import os
from datetime import datetime, timedelta, timezone
from concurrent.futures import ProcessPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from app.database import SessionLocal, init_db
from app.models.paper import Paper
from app.services.arxiv_crawler import _enrich_one, _extract_arxiv_id, _fix_missing_translations

init_db()
db = SessionLocal()

existing_ids = {r[0] for r in db.query(Paper.arxiv_id).all()}
print(f"数据库中已有 {len(existing_ids)} 篇论文")

since = datetime.now(timezone.utc) - timedelta(days=3)
client = arxiv.Client(page_size=200, delay_seconds=3.0)
search = arxiv.Search(
    query="cat:cs.*",
    max_results=3000,
    sort_by=arxiv.SortCriterion.SubmittedDate,
    sort_order=arxiv.SortOrder.Descending,
)

arxiv_papers = []
date_counts = {}
for r in client.results(search):
    pub = r.published
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    if pub < since:
        break
    d = pub.strftime("%Y-%m-%d")
    date_counts[d] = date_counts.get(d, 0) + 1
    aid = _extract_arxiv_id(r.entry_id)
    if aid not in existing_ids:
        arxiv_papers.append({
            "arxiv_id": aid,
            "title": r.title.replace("\n", " ").strip(),
            "abstract": r.summary.replace("\n", " ").strip(),
            "authors": json.dumps([a.name for a in r.authors], ensure_ascii=False),
            "arxiv_url": r.entry_id,
            "pdf_url": r.pdf_url,
            "categories": ",".join(r.categories),
            "published_at": pub,
        })

print(f"\narXiv API 各天论文数量:")
db_dates = {}
for r in db.execute(db.query(Paper).with_entities(
    Paper.published_at,
).statement):
    d = r[0].strftime("%Y-%m-%d") if hasattr(r[0], 'strftime') else str(r[0])[:10]
    db_dates[d] = db_dates.get(d, 0) + 1

for d in sorted(date_counts.keys()):
    api = date_counts[d]
    have = db_dates.get(d, 0)
    diff = api - have
    status = "✅ 完整" if diff == 0 else f"❌ 缺 {diff} 篇"
    print(f"  {d}: arXiv={api}, 数据库={have}  → {status}")

print(f"\n需补爬: {len(arxiv_papers)} 篇")

if not arxiv_papers:
    print("全部爬完，检查翻译...")
    _fix_missing_translations(db)
    db.close()
    exit()

total = len(arxiv_papers)
workers = min(os.cpu_count() or 8, 32, total)
print(f"开始补爬 {total} 篇 (进程数={workers})")

enriched = []
failed = 0
t0 = time.time()
with ProcessPoolExecutor(max_workers=workers) as pool:
    futs = {pool.submit(_enrich_one, p, i, total): p for i, p in enumerate(arxiv_papers, 1)}
    for f in as_completed(futs):
        try:
            enriched.append(f.result())
        except Exception as e:
            failed += 1
            logging.error("增强失败: %s", e)

elapsed = time.time() - t0
m, s = divmod(elapsed, 60)
print(f"\n增强完成: {len(enriched)} 成功, {failed} 失败, 耗时 {int(m)}分{s:.1f}秒")

inserted = 0
for p in enriched:
    try:
        db.add(Paper(**p))
        db.commit()
        inserted += 1
    except Exception as e:
        db.rollback()
        logging.error("入库失败 %s: %s", p.get("arxiv_id"), e)

print(f"入库完成: 新增 {inserted} 篇")

_fix_missing_translations(db)
db.close()
print("\n全部完成!")
