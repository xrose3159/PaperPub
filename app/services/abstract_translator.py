"""论文摘要翻译器 — 使用 Google Translate API，带重试机制。"""

from __future__ import annotations

import logging
import time
import urllib.parse
import urllib.request
import json

logger = logging.getLogger(__name__)

_GT_URL = "https://translate.googleapis.com/translate_a/single"
_MAX_RETRIES = 3


def _google_translate(text: str, sl: str, tl: str) -> str | None:
    """通用 Google Translate 调用，失败自动重试最多 _MAX_RETRIES 次。"""
    if not text or len(text.strip()) < 5:
        return None
    text = text[:4000]
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            params = urllib.parse.urlencode({
                "client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": text,
            })
            url = f"{_GT_URL}?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            translated = "".join(seg[0] for seg in data[0] if seg[0])
            if len(translated) < 5:
                logger.warning("翻译结果过短: %r", translated)
                return None
            return translated
        except Exception as e:
            if attempt < _MAX_RETRIES:
                wait = attempt * 2
                logger.warning("翻译失败 (%s→%s) 第%d次, %ds后重试: %s", sl, tl, attempt, wait, e)
                time.sleep(wait)
            else:
                logger.error("翻译失败 (%s→%s) 已重试%d次放弃: %s", sl, tl, _MAX_RETRIES, e)
                return None


def translate_abstract(abstract: str) -> str | None:
    """英文摘要 → 中文。"""
    return _google_translate(abstract, "en", "zh-CN")


def translate_to_english(text: str) -> str | None:
    """中文文本 → 英文。"""
    return _google_translate(text, "zh-CN", "en")
