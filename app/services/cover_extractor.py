"""论文封面图智能提取器 — arXiv HTML 优先 + PDF 兜底。

工作流：
1. 优先从 arXiv HTML 页面抓取 <figure> 中的图片（质量高，矢量/位图均可）
2. 将候选图发送给视觉大模型，让 AI 挑选最佳的"核心概念图"
3. 若 HTML 不可用，回退到 PDF 提取（区域截图法）
4. 最终兜底：PDF 第一页整页缩略图
"""

from __future__ import annotations

import base64
import logging
import os
import re
from html.parser import HTMLParser
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "data")))
IMAGES_DIR = _DATA_DIR / "paper_images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

TIMEOUT = 30
MAX_CANDIDATES = 5
MIN_IMAGE_BYTES = 3_000
VISION_MODEL = "qwen3.5-plus"

VISION_SYSTEM = "你是一个计算机科学领域的资深研究员，擅长分析学术论文中的图表。"

VISION_PROMPT = (
    "这里有从一篇最新 CS 论文中提取出的 {n} 张候选图片。\n"
    "我需要你帮我挑出最能代表这篇论文核心贡献的「核心概念图 (Teaser Figure)」"
    "或「系统架构图 (Architecture Diagram)」。\n"
    "请注意避开: 学校 Logo、期刊版权图标、纯数据表格、或者无意义的小散点图。\n"
    "请严格只输出一个数字作为你的选择 (例如: 1 代表选择第一张图, "
    "2 代表第二张)。如果所有图片都毫无意义或者只有纯文本, 请输出数字 0。\n"
    "不要输出任何其他标点或解释。"
)

_HEADERS = {"User-Agent": "PaperPub/1.0"}

# ---------- PDF 相关常量 ----------
MIN_DIMENSION = 100
MIN_REGION_RATIO = 0.04
PADDING_PX = 12
RENDER_SCALE = 2.0


# ============================================================
# 1. arXiv HTML 图片提取
# ============================================================

class _FigureParser(HTMLParser):
    """从 arXiv HTML 页面中提取 <figure class="ltx_figure"> 内的 <img> src。"""

    def __init__(self):
        super().__init__()
        self._in_figure = False
        self._depth = 0
        self._fig_class = ""
        self.img_srcs: list[str] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "figure":
            if not self._in_figure:
                self._in_figure = True
                self._fig_class = d.get("class", "")
                self._depth = 1
            else:
                self._depth += 1
        elif self._in_figure and tag == "img":
            src = d.get("src", "")
            if src and "ltx_figure" in self._fig_class:
                self.img_srcs.append(src)

    def handle_endtag(self, tag):
        if self._in_figure and tag == "figure":
            self._depth -= 1
            if self._depth <= 0:
                self._in_figure = False
                self._fig_class = ""


def _extract_from_html(arxiv_id: str) -> list[bytes]:
    """从 arXiv HTML 页面抓取论文 figure 图片，返回图片字节列表。"""
    import random
    import time

    html_url = f"https://arxiv.org/html/{arxiv_id}"
    resp = None
    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(2 + random.random() * 3)
            resp = requests.get(html_url, timeout=TIMEOUT, headers=_HEADERS)
            if resp.status_code == 200:
                break
            logger.info("[%s] HTML 页面不可用 (HTTP %d)", arxiv_id, resp.status_code)
            return []
        except Exception as e:
            if attempt < 2:
                logger.debug("[%s] HTML 请求第 %d 次失败，重试: %s", arxiv_id, attempt + 1, e)
                continue
            logger.warning("[%s] HTML 页面请求失败 (3次重试均失败): %s", arxiv_id, e)
            return []
    if resp is None:
        return []

    parser = _FigureParser()
    parser.feed(resp.text)

    if not parser.img_srcs:
        logger.info("[%s] HTML 页面无 figure 图片", arxiv_id)
        return []

    logger.info("[%s] HTML 页面发现 %d 张 figure 图片", arxiv_id, len(parser.img_srcs))

    candidates: list[bytes] = []
    base_url = f"https://arxiv.org/html/{arxiv_id}/"
    for src in parser.img_srcs[:MAX_CANDIDATES]:
        if src.startswith("http"):
            img_url = src
        elif "/" in src:
            # src 含子路径(如 "2603.04338v1/x1.png")，base 只需到 /html/
            img_url = "https://arxiv.org/html/" + src
        else:
            # src 是纯文件名(如 "x1.png")，需要完整 base
            img_url = base_url + src
        try:
            img_resp = requests.get(img_url, timeout=TIMEOUT, headers=_HEADERS)
            img_resp.raise_for_status()
            img_data = img_resp.content
            if len(img_data) >= MIN_IMAGE_BYTES:
                candidates.append(img_data)
        except Exception as e:
            logger.debug("[%s] 下载图片失败 %s: %s", arxiv_id, src, e)
            continue

    logger.info("[%s] HTML 成功下载 %d 张候选图片", arxiv_id, len(candidates))
    return candidates


# ============================================================
# 2. PDF 提取（兜底）
# ============================================================

def _download_pdf(url: str) -> bytes | None:
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers=_HEADERS)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.warning("下载 PDF 失败 (%s): %s", url, e)
        return None


def _extract_candidates_pdf(pdf_data: bytes) -> list[bytes]:
    """从 PDF 前 5 页提取候选图片（区域截图法，覆盖位图和矢量图）。"""
    import fitz

    doc = fitz.open(stream=pdf_data, filetype="pdf")
    candidates: list[tuple[float, bytes]] = []

    for page_idx in range(min(5, len(doc))):
        page = doc[page_idx]
        page_rect = page.rect
        page_area = page_rect.width * page_rect.height
        mat = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)

        regions: list[fitz.Rect] = []

        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_IMAGES)["blocks"]
        for b in blocks:
            if b.get("type") == 1:
                r = fitz.Rect(b["bbox"])
                if r.width >= MIN_DIMENSION and r.height >= MIN_DIMENSION:
                    regions.append(r)

        drawings = page.get_drawings()
        if drawings:
            draw_rects = [fitz.Rect(d["rect"]) for d in drawings
                          if fitz.Rect(d["rect"]).width > 5 and fitz.Rect(d["rect"]).height > 5]
            merged = _merge_rects(draw_rects, gap=20)
            for mr in merged:
                if mr.width >= MIN_DIMENSION and mr.height >= MIN_DIMENSION:
                    regions.append(mr)

        regions = _deduplicate_rects(regions)
        for r in regions:
            if (r.width * r.height) / page_area < MIN_REGION_RATIO:
                continue
            clip = r + fitz.Rect(-PADDING_PX, -PADDING_PX, PADDING_PX, PADDING_PX)
            clip &= page_rect
            try:
                pix = page.get_pixmap(matrix=mat, clip=clip)
                if pix.n >= 5:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                img_bytes = pix.tobytes("jpeg")
                if len(img_bytes) < MIN_IMAGE_BYTES:
                    continue
                candidates.append((r.width * r.height, img_bytes))
            except Exception:
                continue

    doc.close()
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [img for _, img in candidates[:MAX_CANDIDATES]]


def _merge_rects(rects: list, gap: float = 20) -> list:
    import fitz
    if not rects:
        return []
    merged = [fitz.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        new_merged = []
        used = [False] * len(merged)
        for i in range(len(merged)):
            if used[i]:
                continue
            current = fitz.Rect(merged[i])
            for j in range(i + 1, len(merged)):
                if used[j]:
                    continue
                expanded = current + fitz.Rect(-gap, -gap, gap, gap)
                if expanded.intersects(merged[j]):
                    current |= merged[j]
                    used[j] = True
                    changed = True
            new_merged.append(current)
        merged = new_merged
    return merged


def _deduplicate_rects(rects: list) -> list:
    result = []
    for i, r in enumerate(rects):
        contained = False
        for j, other in enumerate(rects):
            if i != j and other.contains(r) and (other.width * other.height > r.width * r.height):
                contained = True
                break
        if not contained:
            result.append(r)
    return result


def _render_first_page_thumbnail(pdf_data: bytes) -> bytes:
    """将 PDF 第一页上半部分渲染为 JPEG 缩略图（只截取上半，避免大量正文干扰）。"""
    import fitz
    doc = fitz.open(stream=pdf_data, filetype="pdf")
    page = doc[0]
    rect = page.rect
    upper_half = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * 0.5)
    pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=upper_half)
    img_bytes = pix.tobytes("jpeg")
    doc.close()
    return img_bytes


# ============================================================
# 3. Vision LLM 选图
# ============================================================

def _shrink_for_vision(img_data: bytes, max_px: int = 512, quality: int = 60) -> bytes:
    """将图片缩小到 max_px 宽度并降低 JPEG 质量，减少 base64 payload 大小。"""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if img.width > max_px:
            ratio = max_px / img.width
            img = img.resize((max_px, int(img.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        return buf.getvalue()
    except Exception:
        return img_data


def _select_best_image(candidates: list[bytes]) -> int:
    """调用视觉大模型从候选图中选出最佳封面图。返回 1-based 索引，0 表示不合格。"""
    from app.services.llm_client import chat_with_vision

    shrunk = [_shrink_for_vision(img) for img in candidates]
    images_b64 = [base64.b64encode(img).decode() for img in shrunk]
    prompt = VISION_PROMPT.format(n=len(candidates))

    try:
        raw = chat_with_vision(
            system=VISION_SYSTEM,
            user_text=prompt,
            images_base64=images_b64,
            model=VISION_MODEL,
            temperature=0.1,
            max_tokens=256,
        )
        result = raw.strip().strip(".,;:!。，；：！\"' \n")
        digits = re.findall(r"\d+", result)
        if not digits:
            logger.warning("LLM 返回无数字: %r，选择第一张", raw)
            return 1
        choice = int(digits[0])
        if 0 <= choice <= len(candidates):
            return choice
        logger.warning("LLM 返回超范围: %d (共 %d 张)，选择第一张", choice, len(candidates))
        return 1
    except Exception as e:
        logger.error("视觉模型调用失败: %s，选择第一张", e)
        return 1


# ============================================================
# 4. 主入口
# ============================================================

def extract_cover(pdf_url: str, arxiv_id: str, force: bool = False) -> str | None:
    """智能提取论文封面图，返回相对 URL 或 None。

    优先级：arXiv HTML 图片 > PDF 区域截图 > 首页缩略图
    """
    safe_name = arxiv_id.replace("/", "_").replace(".", "_")
    out_path = IMAGES_DIR / f"{safe_name}.jpg"
    relative_url = f"/static/images/papers/{safe_name}.jpg"

    if out_path.exists() and not force:
        return relative_url

    try:
        # ---- 策略 A: arXiv HTML ----
        candidates = _extract_from_html(arxiv_id)
        source = "HTML"

        # ---- 策略 B: PDF 提取 ----
        pdf_data = None
        if not candidates:
            pdf_data = _download_pdf(pdf_url)
            if pdf_data:
                candidates = _extract_candidates_pdf(pdf_data)
                source = "PDF"
                logger.info("[%s] PDF 提取到 %d 张候选图片", arxiv_id, len(candidates))

        # ---- 选择最佳图片 ----
        chosen_img = None
        if candidates:
            if len(candidates) == 1:
                chosen_img = candidates[0]
                logger.info("[%s] (%s) 仅 1 张候选，直接使用", arxiv_id, source)
            else:
                choice = _select_best_image(candidates)
                if 1 <= choice <= len(candidates):
                    chosen_img = candidates[choice - 1]
                    logger.info("[%s] (%s) AI 选择第 %d 张", arxiv_id, source, choice)
                else:
                    chosen_img = candidates[0]
                    logger.info("[%s] (%s) AI 判定不合格，使用第一张", arxiv_id, source)
            del candidates

        # ---- 兜底: 首页缩略图 ----
        if chosen_img is None:
            if pdf_data is None:
                pdf_data = _download_pdf(pdf_url)
            if pdf_data:
                chosen_img = _render_first_page_thumbnail(pdf_data)
                logger.info("[%s] 使用第一页缩略图兜底", arxiv_id)
        del pdf_data

        if chosen_img is None:
            return None

        # 统一转为 JPEG 保存
        _save_as_jpeg(chosen_img, out_path)
        return relative_url

    except Exception as e:
        logger.error("封面提取失败 (%s): %s", arxiv_id, e)
        return None


def _save_as_jpeg(img_data: bytes, out_path: Path):
    """将图片数据保存为 JPEG（如果原图是 PNG 等格式则转换）。"""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_data))
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img.save(str(out_path), "JPEG", quality=90)
    except ImportError:
        out_path.write_bytes(img_data)
