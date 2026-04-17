"""从 HuggingFace Papers API / Models API / 摘要文本提取 GitHub、HuggingFace 链接。

策略层级：
1. HuggingFace Papers API → GitHub 仓库 + Stars
2. HuggingFace Models API (filter=arxiv:ID) → 关联模型 + Likes
3. 摘要文本正则匹配（兜底）
"""

from __future__ import annotations

import logging
import re

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 10

_GH_PATTERN = re.compile(
    r"https?://github\.com/([\w.\-]+/[\w.\-]+)", re.IGNORECASE,
)
_HF_PATTERN = re.compile(
    r"https?://huggingface\.co/"
    r"((?!docs/|blog/|learn/|spaces/|papers/)[\w.\-]+/[\w.\-]+)",
    re.IGNORECASE,
)


def enrich_paper_links(paper: dict) -> dict:
    """为论文 dict 填充 github_url/github_stars/huggingface_url/hf_likes。"""
    arxiv_id = paper.get("arxiv_id", "")
    abstract = paper.get("abstract", "")

    gh_url: str | None = None
    gh_stars: int | None = None
    hf_url: str | None = None
    hf_likes: int | None = None

    # ── 策略 1: HuggingFace Papers API → GitHub ─────────────────
    api_data = _fetch_hf_paper(arxiv_id)
    if api_data:
        if api_data.get("githubRepo"):
            gh_url = api_data["githubRepo"]
            gh_stars = api_data.get("githubStars")
            logger.info("⭐ [HF-API] %s → %s (%s stars)", arxiv_id, gh_url, gh_stars)
        if api_data.get("projectPage") and not gh_url:
            proj_gh = _GH_PATTERN.search(api_data["projectPage"])
            if proj_gh:
                gh_url = f"https://github.com/{proj_gh.group(1)}"

    # ── 策略 2: HuggingFace Models API → 关联模型 ───────────────
    if not hf_url:
        hf_url, hf_likes = _search_hf_model(arxiv_id)
        if hf_url:
            logger.info(
                "🤗 [HF-Model] %s → %s (%s likes)", arxiv_id, hf_url, hf_likes,
            )

    # ── 策略 3: 摘要文本正则 ────────────────────────────────────
    if not gh_url or not hf_url:
        gh_txt, hf_txt = _find_links_in_text(abstract)
        gh_url = gh_url or gh_txt
        hf_url = hf_url or hf_txt

    # ── 补全 Stars / Likes ──────────────────────────────────────
    if gh_url:
        paper["github_url"] = gh_url
        if gh_stars is not None:
            paper["github_stars"] = gh_stars
        else:
            stars = _get_github_stars(gh_url)
            if stars is not None:
                paper["github_stars"] = stars
                logger.info("⭐ GitHub: %s → %d stars", gh_url, stars)

    if hf_url:
        paper["huggingface_url"] = hf_url
        if hf_likes is not None:
            paper["hf_likes"] = hf_likes
        else:
            likes = _get_hf_likes(hf_url)
            if likes is not None:
                paper["hf_likes"] = likes

    return paper


# ═══════════════════════════════════════════════════════════════════
# HuggingFace Papers API
# ═══════════════════════════════════════════════════════════════════

def _fetch_hf_paper(arxiv_id: str) -> dict | None:
    if not arxiv_id:
        return None
    try:
        resp = requests.get(
            f"https://huggingface.co/api/papers/{arxiv_id}", timeout=TIMEOUT,
        )
        if resp.ok:
            return resp.json()
    except Exception as e:
        logger.debug("HF Papers API error for %s: %s", arxiv_id, e)
    return None


# ═══════════════════════════════════════════════════════════════════
# HuggingFace Models / Datasets 搜索（通过 arxiv 标签）
# ═══════════════════════════════════════════════════════════════════

def _search_hf_model(arxiv_id: str) -> tuple[str | None, int | None]:
    """通过 arxiv paper tag 搜索关联的 HF 模型，返回 (url, likes)。"""
    if not arxiv_id:
        return None, None

    # 搜索模型（按 likes 排序取第一个）
    try:
        resp = requests.get(
            "https://huggingface.co/api/models",
            params={"filter": f"arxiv:{arxiv_id}", "limit": 1,
                    "sort": "likes", "direction": "-1"},
            timeout=TIMEOUT,
        )
        if resp.ok:
            models = resp.json()
            if models:
                m = models[0]
                url = f"https://huggingface.co/{m['id']}"
                return url, m.get("likes", 0)
    except Exception:
        pass

    # 搜索数据集
    try:
        resp = requests.get(
            "https://huggingface.co/api/datasets",
            params={"filter": f"arxiv:{arxiv_id}", "limit": 1,
                    "sort": "likes", "direction": "-1"},
            timeout=TIMEOUT,
        )
        if resp.ok:
            datasets = resp.json()
            if datasets:
                d = datasets[0]
                url = f"https://huggingface.co/datasets/{d['id']}"
                return url, d.get("likes", 0)
    except Exception:
        pass

    return None, None


# ═══════════════════════════════════════════════════════════════════
# 文本正则匹配
# ═══════════════════════════════════════════════════════════════════

def _find_links_in_text(text: str) -> tuple[str | None, str | None]:
    gh_match = _GH_PATTERN.search(text)
    hf_match = _HF_PATTERN.search(text)
    gh_url = f"https://github.com/{gh_match.group(1)}" if gh_match else None
    hf_url = f"https://huggingface.co/{hf_match.group(1)}" if hf_match else None
    return gh_url, hf_url


# ═══════════════════════════════════════════════════════════════════
# GitHub / HuggingFace Stars / Likes
# ═══════════════════════════════════════════════════════════════════

def _get_github_stars(gh_url: str) -> int | None:
    match = _GH_PATTERN.search(gh_url)
    if not match:
        return None
    repo_path = match.group(1).rstrip("/.")
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo_path}",
            timeout=TIMEOUT,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if resp.ok:
            return resp.json().get("stargazers_count")
    except Exception:
        pass
    return None


def _get_hf_likes(hf_url: str) -> int | None:
    match = _HF_PATTERN.search(hf_url)
    if not match:
        return None
    repo_path = match.group(1)
    for api_url in [
        f"https://huggingface.co/api/models/{repo_path}",
        f"https://huggingface.co/api/datasets/{repo_path}",
    ]:
        try:
            resp = requests.get(api_url, timeout=TIMEOUT)
            if resp.ok:
                likes = resp.json().get("likes")
                if likes is not None:
                    return likes
        except Exception:
            continue
    return None


# ═══════════════════════════════════════════════════════════════════
# 旧接口兼容
# ═══════════════════════════════════════════════════════════════════

def find_github_hf_links(
    abstract: str, arxiv_id: str,
) -> tuple[str | None, str | None]:
    gh_url: str | None = None
    hf_url: str | None = None

    api_data = _fetch_hf_paper(arxiv_id)
    if api_data:
        gh_url = api_data.get("githubRepo")
        if not gh_url and api_data.get("projectPage"):
            m = _GH_PATTERN.search(api_data["projectPage"])
            if m:
                gh_url = f"https://github.com/{m.group(1)}"

    if not hf_url:
        hf_url, _ = _search_hf_model(arxiv_id)

    gh_txt, hf_txt = _find_links_in_text(abstract)
    gh_url = gh_url or gh_txt
    hf_url = hf_url or hf_txt
    return gh_url, hf_url


def get_github_stars(gh_url: str) -> int | None:
    return _get_github_stars(gh_url)


def get_hf_likes(hf_url: str) -> int | None:
    return _get_hf_likes(hf_url)
