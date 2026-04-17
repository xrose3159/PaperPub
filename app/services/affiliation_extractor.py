"""论文机构提取：HTML 解析优先（快速），PDF+LLM 兜底（准确）。

策略层级：
1. 解析 arXiv /html/ 页面中 ltx_role_affiliation / footnote（最快，~2s）
2. 下载 PDF → 提取第一页文本 → LLM 精确提取（最准确，~15-30s）
3. LLM 仅凭标题 + 作者推断（最后兜底）
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile

import requests

from app.services.llm_client import chat

logger = logging.getLogger(__name__)

TIMEOUT = 30
EXTRACT_MODEL = "qwen3.5-plus"

# ── LaTeX 噪声清理 ──────────────────────────────────────────────
_LATEX_NOISE = re.compile(
    r"\{?\}?\^?\{?\\text\{.*?\}\}?"
    r"|start_FLOAT(?:SUPER|SUB)SCRIPT.*?end_FLOAT(?:SUPER|SUB)SCRIPT"
    r"|\$[^$]*\$"
    r"|\\[a-zA-Z]+(?:\{[^}]*\})?"
    r"|[{}]",
    re.DOTALL,
)

_INST_KEYWORDS = {
    "university", "univ.", "institute", "institution", "laboratory",
    "lab", "college", "school", "center", "centre", "department",
    "dept", "faculty", "research",
    "microsoft", "google", "meta", "nvidia", "amazon", "apple",
    "deepmind", "openai", "tencent", "alibaba", "baidu", "huawei",
    "bytedance", "samsung", "ibm", "intel", "qualcomm", "adobe",
    "salesforce", "stability", "anthropic", "mistral", "fair",
}

_LLM_SYSTEM_PROMPT = (
    "You are an expert at extracting research institution affiliations "
    "from academic paper text.\n"
    "Rules:\n"
    "- Return a JSON array of institution names (strings)\n"
    "- Include universities, companies, labs, research centers\n"
    "- Use the full official name when available\n"
    "- Deduplicate, order by appearance\n"
    "- Max 10 institutions\n"
    "- If no affiliations found, return []\n"
    "- Output ONLY the raw JSON array, no markdown fences, no explanation"
)


# ═══════════════════════════════════════════════════════════════════
# 公开入口
# ═══════════════════════════════════════════════════════════════════

def extract_affiliations(
    arxiv_id: str, title: str = "", authors: str = "",
) -> list[str] | None:
    """多策略提取论文机构。HTML 优先（快） → PDF+LLM（准） → LLM 推断。"""

    affs = _from_arxiv_html(arxiv_id)
    if affs:
        return affs

    affs = _from_pdf(arxiv_id)
    if affs:
        return affs

    return _from_llm_inference(arxiv_id, title, authors)


# ═══════════════════════════════════════════════════════════════════
# 策略 1: PDF 第一页 + LLM
# ═══════════════════════════════════════════════════════════════════

def _from_pdf(arxiv_id: str) -> list[str] | None:
    """下载 PDF，用 pymupdf 提取第一页文本，交给 LLM 解析机构。"""
    text = _fetch_pdf_first_page(arxiv_id)
    if not text:
        return None

    affs = _llm_extract(text[:3000], source_label="PDF")
    if affs:
        logger.info("🏛️ [PDF+LLM] %s → %s", arxiv_id, affs[:5])
    return affs


def _fetch_pdf_first_page(arxiv_id: str) -> str | None:
    """下载 arXiv PDF 并提取第一页纯文本。"""
    try:
        import fitz  # pymupdf
    except ImportError:
        logger.warning("pymupdf not installed, skipping PDF extraction")
        return None

    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    try:
        resp = requests.get(pdf_url, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code != 200:
            return None
    except Exception:
        return None

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(resp.content)
            tmp_path = f.name
        doc = fitz.open(tmp_path)
        if len(doc) == 0:
            doc.close()
            return None
        text = doc[0].get_text()
        doc.close()
        return text if text and len(text.strip()) > 50 else None
    except Exception as e:
        logger.debug("PDF extraction failed for %s: %s", arxiv_id, e)
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════════
# 策略 2: arXiv HTML 解析
# ═══════════════════════════════════════════════════════════════════

def _from_arxiv_html(arxiv_id: str) -> list[str] | None:
    """从 arXiv /html/ 全文版提取机构。"""
    html = _fetch_html(arxiv_id)
    if not html:
        return None

    affs = _parse_ltx_affiliations(html)
    if affs:
        logger.info("🏛️ [HTML] %s → %s", arxiv_id, affs[:5])
        return affs

    affs = _parse_footnote_affiliations(html)
    if affs:
        logger.info("🏛️ [footnote] %s → %s", arxiv_id, affs[:5])
        return affs

    return None


def _fetch_html(arxiv_id: str) -> str | None:
    for suffix in ["v1", "v2", "v3", ""]:
        url = f"https://arxiv.org/html/{arxiv_id}{suffix}"
        try:
            resp = requests.get(url, timeout=TIMEOUT, allow_redirects=True)
            if resp.ok and len(resp.text) > 1000:
                return resp.text
        except Exception:
            continue
    return None


def _parse_ltx_affiliations(html: str) -> list[str] | None:
    raw_blocks = re.findall(
        r'class="[^"]*ltx_role_affiliation[^"]*"[^>]*>(.*?)</(?:span|div)>',
        html, re.DOTALL,
    )
    if not raw_blocks:
        return None

    first_block = raw_blocks[0]
    cleaned = re.sub(r"<[^>]+>", " ", first_block)
    cleaned = _LATEX_NOISE.sub(" ", cleaned)
    cleaned = re.sub(r"(?<=[,])\s*\d+(?:\s+\d+)*\s+", " ", cleaned)
    cleaned = re.sub(r"\s+\d+(?:\s+\d+)*\s*(?=[,])", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^\s*\d[\d\s]*", "", cleaned)
    cleaned = re.sub(r"[\d\s]*\d\s*$", "", cleaned)

    if not cleaned:
        return None

    parts = [p.strip() for p in cleaned.split(",")]
    result: list[str] = []
    seen: set[str] = set()
    buf = ""
    for p in parts:
        p = p.strip(" ;.\t\n")
        if not p or p.isdigit() or len(p) < 2:
            continue
        if re.fullmatch(r"[\d\s]+", p):
            continue
        if (buf
            and not any(kw in p.lower() for kw in _INST_KEYWORDS)
            and p[0].islower()):
            buf = f"{buf}, {p}"
        else:
            if buf:
                _add_unique(result, seen, buf)
            buf = p
    if buf:
        _add_unique(result, seen, buf)

    return result[:10] if result else None


def _add_unique(result: list[str], seen: set[str], name: str) -> None:
    name = re.sub(r"^\d[\d\s]*", "", name).strip(" ,;.")
    name = re.sub(r"[\d\s]*\d$", "", name).strip(" ,;.")
    if len(name) < 2:
        return
    key = name.lower()
    if key not in seen:
        seen.add(key)
        result.append(name)


def _parse_footnote_affiliations(html: str) -> list[str] | None:
    header_end = html.find('id="S1"')
    if header_end < 0:
        header_end = html.find('id="abstract')
    if header_end < 0:
        header_end = min(len(html), 15000)

    header = html[:header_end]
    footnotes = re.findall(
        r'class="[^"]*ltx_note_content[^"]*"[^>]*>(.*?)</span>',
        header, re.DOTALL,
    )
    if not footnotes:
        footnotes = re.findall(
            r'class="[^"]*ltx_role_footnote[^"]*"[^>]*>(.*?)</span>',
            header, re.DOTALL,
        )

    result: list[str] = []
    seen: set[str] = set()
    for fn in footnotes:
        text = re.sub(r"<[^>]+>", " ", fn)
        text = _LATEX_NOISE.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if re.search(r"institutetext|\\inst\b", text, re.IGNORECASE):
            continue
        if len(text) < 5 or len(text) > 200:
            continue
        if any(kw in text.lower() for kw in _INST_KEYWORDS):
            key = text.lower()
            if key not in seen:
                seen.add(key)
                result.append(text)

    return result[:10] if result else None


# ═══════════════════════════════════════════════════════════════════
# 策略 3: LLM 纯推断（仅凭标题 + 作者）
# ═══════════════════════════════════════════════════════════════════

def _from_llm_inference(
    arxiv_id: str, title: str, authors: str,
) -> list[str] | None:
    if not authors:
        return None

    system = (
        "你是学术文献分析专家。根据论文标题和作者列表，"
        "推断作者最可能来自的研究机构（大学、实验室或公司）。\n"
        "要求：\n"
        "- 返回 JSON 数组，每个元素是机构英文简称或全称\n"
        "- 去重，按重要性降序\n"
        "- 最多 5 个\n"
        "- 无法判断则返回 []\n"
        "- 只输出 JSON 数组，不要任何解释或 markdown 格式"
    )
    affs = _llm_extract(
        f"Title: {title}\nAuthors: {authors}",
        source_label="LLM-inference",
        system_override=system,
    )
    if affs:
        logger.info("🏛️ [LLM] %s → %s", arxiv_id, affs[:3])
    return affs


# ═══════════════════════════════════════════════════════════════════
# 共享 LLM 调用 + 鲁棒 JSON 解析
# ═══════════════════════════════════════════════════════════════════

def _llm_extract(
    user_text: str,
    source_label: str = "",
    system_override: str | None = None,
) -> list[str] | None:
    """调用 LLM 并鲁棒地解析 JSON 数组结果。"""
    try:
        raw = chat(
            system=system_override or _LLM_SYSTEM_PROMPT,
            user=user_text,
            model=EXTRACT_MODEL,
            temperature=0.1,
            max_tokens=512,
        )
    except Exception as e:
        logger.debug("LLM call failed [%s]: %s", source_label, e)
        return None

    return _parse_json_array(raw, source_label)


def _parse_json_array(raw: str, label: str = "") -> list[str] | None:
    """从 LLM 输出中鲁棒地提取 JSON 数组。"""
    text = raw.strip()

    # 去掉 markdown 代码块
    md = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if md:
        text = md.group(1).strip()

    # 提取 [...] 部分
    arr_match = re.search(r"\[.*]", text, re.DOTALL)
    if arr_match:
        text = arr_match.group(0)

    # 修复被截断的 JSON（尾部缺少 ] 或引号）
    if text.startswith("[") and not text.endswith("]"):
        last_quote = text.rfind('"')
        if last_quote > 0:
            text = text[:last_quote + 1] + "]"

    # 清除控制字符
    text = re.sub(r"[\x00-\x1f]", " ", text)

    try:
        arr = json.loads(text)
    except json.JSONDecodeError:
        # 最后尝试：逐行提取引号内的字符串
        arr = re.findall(r'"([^"]+)"', text)
        if not arr:
            logger.debug("JSON parse failed [%s]: %s", label, text[:200])
            return None

    if isinstance(arr, list) and arr:
        return [str(a).strip() for a in arr if a and str(a).strip()]
    return None
