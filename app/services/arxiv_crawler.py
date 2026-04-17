"""arXiv CS 论文爬虫：使用 requests + XML 解析直接调用 arXiv API。

双层并行架构：
- 外层 ProcessPoolExecutor：多进程并行处理多篇论文（利用多核 CPU）
- 内层 ThreadPoolExecutor：每篇论文的 6 项增强步骤并行执行（I/O 密集型）
- 主进程串行写入数据库
"""

from __future__ import annotations

import json
import logging
import os
import time as _time
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests as _requests
from sqlalchemy.orm import Session

from app.database import SessionLocal, init_db
from app.models.paper import Paper

logger = logging.getLogger(__name__)

PROC_WORKERS = min(os.cpu_count() or 32, 32)
STEP_WORKERS = 6
FETCH_TIMEOUT = int(os.getenv("ARXIV_FETCH_TIMEOUT", "600"))  # 拉取最大秒数，默认10分钟

# arXiv Atom XML 命名空间
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

_ARXIV_API_BASE = "http://export.arxiv.org/api/query"


def _extract_arxiv_id(entry_id: str) -> str:
    """从 entry_id URL 中提取纯 arxiv_id，去掉版本号。"""
    raw = entry_id.split("/abs/")[-1]
    if "v" in raw:
        raw = raw[: raw.rfind("v")]
    return raw


def _parse_entries(xml_text: str) -> list[dict]:
    """解析 arXiv Atom XML 响应，返回论文字典列表。"""
    root = ET.fromstring(xml_text)
    entries = root.findall("atom:entry", _NS)
    papers = []
    for entry in entries:
        entry_id = entry.findtext("atom:id", "", _NS).strip()
        if not entry_id:
            continue

        title = entry.findtext("atom:title", "", _NS).replace("\n", " ").strip()
        abstract = entry.findtext("atom:summary", "", _NS).replace("\n", " ").strip()
        published = entry.findtext("atom:published", "", _NS).strip()

        # 解析作者
        authors = []
        for author_el in entry.findall("atom:author", _NS):
            name = author_el.findtext("atom:name", "", _NS).strip()
            if name:
                authors.append(name)

        # 解析 PDF 链接
        pdf_url = ""
        for link_el in entry.findall("atom:link", _NS):
            if link_el.get("title") == "pdf":
                pdf_url = link_el.get("href", "")
                break

        # 解析分类
        categories = []
        for cat_el in entry.findall("atom:category", _NS):
            term = cat_el.get("term", "")
            if term:
                categories.append(term)

        # 解析发布日期
        try:
            pub_date = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except Exception:
            pub_date = datetime.now(timezone.utc)
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=timezone.utc)

        papers.append({
            "arxiv_id": _extract_arxiv_id(entry_id),
            "title": title,
            "abstract": abstract,
            "authors": json.dumps(authors, ensure_ascii=False),
            "arxiv_url": entry_id,
            "pdf_url": pdf_url,
            "categories": ",".join(categories),
            "published_at": pub_date,
        })
    return papers


def fetch_papers(
    category: str = "cs.*",
    max_results: int = 5000,
    days_back: int = 5,
) -> list[dict]:
    """从 arXiv API 拉取指定类别的最近论文，使用 requests 直接请求 + XML 解析。"""
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    query = f"cat:{category}"

    # 支持国内镜像
    arxiv_mirror = os.getenv("ARXIV_MIRROR", "")
    base_url = f"{arxiv_mirror.rstrip('/')}/api/query" if arxiv_mirror else _ARXIV_API_BASE
    if arxiv_mirror:
        logger.info("使用 arXiv 镜像: %s", arxiv_mirror)

    page_size = 100
    papers: list[dict] = []
    t_start = _time.time()
    start = 0

    session = _requests.Session()
    session.headers.update({"User-Agent": "PaperPub/1.0"})

    while start < max_results:
        # 总超时保护
        if _time.time() - t_start > FETCH_TIMEOUT:
            logger.warning("arXiv 拉取超时 (%ds)，已获取 %d 篇，停止拉取", FETCH_TIMEOUT, len(papers))
            break

        batch_size = min(page_size, max_results - start)
        params = {
            "search_query": query,
            "start": start,
            "max_results": batch_size,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        url = f"{base_url}?{urlencode(params)}"
        logger.info("请求 arXiv API: start=%d, max_results=%d", start, batch_size)

        try:
            resp = session.get(url, timeout=120)
            resp.raise_for_status()
        except Exception as e:
            logger.error("arXiv API 请求失败: %s（已获取 %d 篇）", e, len(papers))
            break

        batch = _parse_entries(resp.text)
        if not batch:
            logger.info("arXiv API 返回空结果，停止拉取 (start=%d)", start)
            break

        # 过滤日期
        for p in batch:
            if p["published_at"] >= since:
                papers.append(p)

        # 如果本页返回数量少于请求数量，说明已经没有更多了
        if len(batch) < batch_size:
            logger.info("arXiv API 返回 %d 篇 (< %d)，已到末尾", len(batch), batch_size)
            break

        start += batch_size
        # arXiv API 要求间隔 3 秒
        _time.sleep(3)

    session.close()
    elapsed = _time.time() - t_start
    logger.info("从 arXiv 拉取到 %d 篇论文 (%.1f秒)", len(papers), elapsed)
    return papers


# ── 单篇增强（在子进程中执行，内部 6 步并行） ──────────────────

def _enrich_one(p: dict, idx: int, total: int) -> dict:
    """单篇论文全部增强。6 项步骤在线程池中并行执行，最大化 I/O 吞吐。"""
    from app.services.affiliation_extractor import extract_affiliations
    from app.services.contribution_extractor import extract_contribution
    from app.services.cover_extractor import extract_cover
    from app.services.external_links import enrich_paper_links
    from app.services.paper_classifier import classify_paper_with_llm

    t_start = _time.time()
    label = f"[{idx}/{total}]"
    print(f"\n🚀 {label} PID={os.getpid()} 处理: {p['title'][:80]}")

    def _classify():
        t = _time.time()
        tags = classify_paper_with_llm(p["title"], p["abstract"])
        print(f"🏷️ {label} 分类: {tags} ({_time.time()-t:.1f}s)")
        return ("classify", tags)

    def _contribution():
        t = _time.time()
        contrib = extract_contribution(p["title"], p["abstract"])
        en = None
        print(f"💡 {label} 贡献提取 ({_time.time()-t:.1f}s)")
        return ("contribution", (contrib, en))

    def _cover():
        t = _time.time()
        cover = extract_cover(p["pdf_url"], p["arxiv_id"]) if p.get("pdf_url") else None
        print(f"🖼️ {label} 封面 ({_time.time()-t:.1f}s)")
        return ("cover", cover)

    def _links():
        t = _time.time()
        tmp = dict(p)
        enrich_paper_links(tmp)
        result = {k: tmp.get(k) for k in ("github_url", "github_stars", "huggingface_url", "hf_likes")}
        gh = f"GH={result.get('github_stars', '-')}" if result.get("github_url") else "GH=无"
        hf = f"HF={result.get('hf_likes', '-')}" if result.get("huggingface_url") else "HF=无"
        print(f"🔗 {label} 链接: {gh}, {hf} ({_time.time()-t:.1f}s)")
        return ("links", result)

    def _affiliations():
        t = _time.time()
        affs = extract_affiliations(p["arxiv_id"], title=p["title"], authors=p["authors"])
        if affs:
            print(f"🏛️ {label} 机构: {', '.join(affs[:3])} ({_time.time()-t:.1f}s)")
        else:
            print(f"🏛️ {label} 机构: 无 ({_time.time()-t:.1f}s)")
        return ("affiliations", affs)

    steps = [_classify, _contribution, _cover, _links, _affiliations]

    with ThreadPoolExecutor(max_workers=STEP_WORKERS) as pool:
        futs = {pool.submit(fn): fn.__name__ for fn in steps}
        for fut in as_completed(futs):
            step_name = futs[fut]
            try:
                key, val = fut.result()
                if key == "classify":
                    p["ai_tags"] = val
                    p["ai_category"] = val[0] if val else "Core ML"
                elif key == "contribution":
                    contrib, en = val
                    if contrib:
                        p["core_contribution"] = contrib
                    if en:
                        p["core_contribution_en"] = en
                elif key == "cover":
                    if val:
                        p["cover_image_url"] = val
                elif key == "links":
                    for k, v in val.items():
                        if v is not None:
                            p[k] = v
                elif key == "affiliations":
                    if val:
                        p["affiliations"] = val
            except Exception as exc:
                logger.error("❌ %s 步骤 %s 失败: %s", label, step_name, exc)

    total_sec = _time.time() - t_start
    print(f"⏱️ {label} 单篇总计: {total_sec:.1f}s")
    return p


def save_papers(papers: list[dict], db: Session | None = None) -> int:
    """多进程并发增强论文，主进程逐篇写入数据库。"""
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        existing_ids = {r[0] for r in db.query(Paper.arxiv_id).all()}
        new_papers = [p for p in papers if p["arxiv_id"] not in existing_ids]
        skipped = len(papers) - len(new_papers)
        if skipped:
            logger.info("跳过 %d 篇已存在的论文", skipped)
        if not new_papers:
            logger.info("没有新论文需要处理")
            return 0

        total = len(new_papers)
        workers = min(PROC_WORKERS, total)
        print(f"\n{'='*60}")
        print(f"📰 开始并行处理 {total} 篇新论文")
        print(f"   线程数={workers}, 每篇内部线程数={STEP_WORKERS}")
        print(f"   CPU 核心数={os.cpu_count()}")
        print(f"{'='*60}")

        inserted = 0
        failed = 0
        t_enrich = _time.time()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {
                pool.submit(_enrich_one, p, i, total): p
                for i, p in enumerate(new_papers, 1)
            }
            for future in as_completed(future_map):
                p = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:
                    failed += 1
                    logger.error("❌ 增强论文 %s 失败: %s", p.get("arxiv_id", "?"), exc)
                    continue
                try:
                    db.add(Paper(**result))
                    db.commit()
                    inserted += 1
                    print(f"✅ 已入库: {result.get('title','?')[:60]} (累计 {inserted})")
                except Exception as exc:
                    db.rollback()
                    logger.error("❌ 入库论文 %s 失败: %s", result.get("arxiv_id", "?"), exc)

        enrich_sec = _time.time() - t_enrich
        enrich_m, enrich_s = divmod(enrich_sec, 60)
        print(f"\n🔄 完成: {inserted} 篇入库, {failed} 篇增强失败, "
              f"耗时 {int(enrich_m)}分{enrich_s:.1f}秒")

        logger.info(
            "本次爬取完成：新增 %d 篇，增强失败 %d 篇", inserted, failed,
        )

        return inserted
    finally:
        if own_session:
            db.close()


def crawl(
    category: str = "cs.*",
    max_results: int = 5000,
    days_back: int = 5,
) -> int:
    """一键爬取 + 入库，返回新增数量。"""
    init_db()
    papers = fetch_papers(category=category, max_results=max_results, days_back=days_back)
    return save_papers(papers)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("=" * 60)
    print(f"📰 arXiv 论文爬取 (多进程×多线程并行)")
    print(f"   PROC_WORKERS={PROC_WORKERS}, STEP_WORKERS={STEP_WORKERS}")
    print("=" * 60)
    _t0 = _time.time()
    count = crawl(max_results=5000, days_back=5)
    _elapsed = _time.time() - _t0
    _m, _s = divmod(_elapsed, 60)
    print(f"\n{'='*60}")
    print(f"✅ 成功抓取并处理 {count} 篇论文，总耗时: {int(_m)}分{_s:.1f}秒")
    print(f"{'='*60}")
