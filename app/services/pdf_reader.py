"""PDF 下载与文本提取 — 从 arXiv 下载论文并提取纯文本。"""

from __future__ import annotations

import io
import logging
import re
import tempfile
from pathlib import Path

import requests
from pdfminer.high_level import extract_text

logger = logging.getLogger(__name__)

CACHE_DIR = Path(tempfile.gettempdir()) / "paperreview_pdf_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

MAX_CHARS = 30_000
DOWNLOAD_TIMEOUT = 60


def _cached_path(arxiv_id: str) -> Path:
    safe = arxiv_id.replace("/", "_").replace(".", "_")
    return CACHE_DIR / f"{safe}.pdf"


def download_pdf(pdf_url: str, arxiv_id: str) -> Path | None:
    """下载 PDF 到本地缓存目录，返回文件路径。已缓存则跳过。"""
    cached = _cached_path(arxiv_id)
    if cached.exists() and cached.stat().st_size > 1000:
        logger.debug("PDF 缓存命中: %s", cached)
        return cached

    try:
        logger.info("📥 下载 PDF: %s", pdf_url)
        resp = requests.get(pdf_url, timeout=DOWNLOAD_TIMEOUT, stream=True)
        resp.raise_for_status()

        with open(cached, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info("📥 PDF 已保存: %s (%.1f KB)", cached.name, cached.stat().st_size / 1024)
        return cached
    except Exception as e:
        logger.warning("📥 PDF 下载失败 [%s]: %s", arxiv_id, e)
        if cached.exists():
            cached.unlink(missing_ok=True)
        return None


def extract_pdf_text(pdf_path: Path) -> str:
    """从 PDF 文件中提取纯文本。"""
    try:
        text = extract_text(str(pdf_path))
        return text or ""
    except Exception as e:
        logger.warning("PDF 文本提取失败 [%s]: %s", pdf_path.name, e)
        return ""


def _clean_text(raw: str) -> str:
    """清理 PDF 提取的文本：合并多余空行、去除乱码。"""
    text = re.sub(r"\x0c", "\n", raw)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def get_paper_fulltext(pdf_url: str | None, arxiv_id: str, max_chars: int = MAX_CHARS) -> str:
    """完整流程：下载 PDF → 提取文本 → 清理 → 截断。

    Returns:
        截断后的论文正文文本。如果失败则返回空字符串。
    """
    if not pdf_url:
        logger.warning("论文 %s 无 PDF 链接", arxiv_id)
        return ""

    pdf_path = download_pdf(pdf_url, arxiv_id)
    if not pdf_path:
        return ""

    raw_text = extract_pdf_text(pdf_path)
    if not raw_text:
        return ""

    cleaned = _clean_text(raw_text)

    if max_chars and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "\n\n[...论文正文已截断至前 {} 字符...]".format(max_chars)
        logger.info("论文 %s 正文已截断: %d -> %d 字符", arxiv_id, len(raw_text), max_chars)

    return cleaned
