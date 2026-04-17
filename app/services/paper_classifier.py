"""论文 AI 分类器 — 使用 LLM 为论文打上 1~3 个多标签（14 类体系）。"""

from __future__ import annotations

import json
import logging
import re

from app.services.llm_client import chat

logger = logging.getLogger(__name__)

VALID_TAGS = [
    "Foundation",
    "Generative",
    "Multimodal",
    "Reasoning",
    "Agents",
    "Core ML",
    "Efficiency",
    "Systems",
    "AI Infra",
    "Alignment & Safety",
    "Data & Benchmark",
    "Math & Code",
    "AI for Science",
    "Embodied AI",
]

VALID_TAGS_SET = set(VALID_TAGS)

CLASSIFY_MODEL = "qwen3.5-plus"

SYSTEM_PROMPT = (
    "You are a top-tier AI research scientist. Based on the paper's title and abstract, "
    "assign 1 to 3 classification tags from the list below, ordered by relevance.\n\n"
    "Available tags (14 categories):\n"
    '["Foundation", "Generative", "Multimodal", "Reasoning", "Agents", "Core ML", '
    '"Efficiency", "Systems", "AI Infra", "Alignment & Safety", '
    '"Data & Benchmark", "Math & Code", "AI for Science", "Embodied AI"]\n\n'
    "Tag descriptions:\n"
    "- Foundation: base model architecture innovation, pre-training mechanisms, new LLM releases\n"
    "- Generative: generative algorithms & mechanisms — Diffusion, Flow Matching, autoregressive generation, VAE, GAN\n"
    "- Multimodal: vision-language, audio-language, video understanding, cross-modal fusion\n"
    "- Reasoning: chain-of-thought, logical/causal reasoning, planning, self-reflection\n"
    "- Agents: autonomous agents, multi-agent systems, tool use, RAG, function calling\n"
    "- Core ML: classical ML theory, optimization, representation learning, loss functions\n"
    "- Efficiency: parameter-efficient fine-tuning (LoRA, Adapter), quantization, pruning, distillation\n"
    "- Systems: inference frameworks, serving systems, scheduling, parallelism strategies\n"
    "- AI Infra: hardware-software co-design, GPU/TPU clusters, networking, compiler optimization\n"
    "- Alignment & Safety: RLHF, DPO, value alignment, jailbreak defense, bias/hallucination mitigation\n"
    "- Data & Benchmark: dataset construction, synthetic data, data curation, evaluation benchmarks\n"
    "- Math & Code: mathematical reasoning, theorem proving, code generation, program synthesis\n"
    "- AI for Science: AI for physics, chemistry, biology, medicine, materials, climate\n"
    "- Embodied AI: robotics, manipulation, autonomous driving, simulation, physical interaction\n\n"
    "Rules:\n"
    "1. Output a valid JSON array of strings. Example: [\"Foundation\", \"Efficiency\"]\n"
    "2. Use 1 tag if the paper clearly belongs to one area; use 2-3 if it genuinely spans multiple.\n"
    "3. Do NOT output any markdown formatting, explanation, or extra characters. Pure JSON array only."
)

_FUZZY_MAP: dict[str, str] = {
    "foundation":       "Foundation",
    "foundation model":  "Foundation",
    "generative":       "Generative",
    "diffusion":        "Generative",
    "flow matching":    "Generative",
    "multimodal":       "Multimodal",
    "vision-language":  "Multimodal",
    "reasoning":        "Reasoning",
    "chain-of-thought": "Reasoning",
    "agents":           "Agents",
    "agent":            "Agents",
    "tool use":         "Agents",
    "rag":              "Agents",
    "core ml":          "Core ML",
    "machine learning": "Core ML",
    "optimization":     "Core ML",
    "efficiency":       "Efficiency",
    "quantization":     "Efficiency",
    "lora":             "Efficiency",
    "fine-tuning":      "Efficiency",
    "systems":          "Systems",
    "serving":          "Systems",
    "inference framework": "Systems",
    "ai infra":         "AI Infra",
    "infrastructure":   "AI Infra",
    "hardware":         "AI Infra",
    "alignment":        "Alignment & Safety",
    "safety":           "Alignment & Safety",
    "rlhf":             "Alignment & Safety",
    "data":             "Data & Benchmark",
    "benchmark":        "Data & Benchmark",
    "dataset":          "Data & Benchmark",
    "math":             "Math & Code",
    "code generation":  "Math & Code",
    "theorem":          "Math & Code",
    "science":          "AI for Science",
    "biology":          "AI for Science",
    "chemistry":        "AI for Science",
    "physics":          "AI for Science",
    "embodied":         "Embodied AI",
    "robotics":         "Embodied AI",
    "robot":            "Embodied AI",
    "autonomous driving": "Embodied AI",
}


def _fuzzy_resolve(raw: str) -> str | None:
    """Try to resolve a raw string to a valid tag via fuzzy matching."""
    stripped = raw.strip()
    if stripped in VALID_TAGS_SET:
        return stripped
    lower = stripped.lower()
    for key, tag in _FUZZY_MAP.items():
        if key in lower or lower in key:
            return tag
    return None


def _repair_truncated_json(s: str) -> list | None:
    """Attempt to repair truncated JSON arrays like '["A", "B & C'."""
    s = s.strip()
    if not s.startswith("["):
        return None
    if s.endswith("]"):
        try:
            result = json.loads(s)
            return result if isinstance(result, list) else None
        except json.JSONDecodeError:
            pass

    items = re.findall(r'"([^"]+)"', s)
    return items if items else None


def classify_paper_with_llm(title: str, abstract: str) -> list[str]:
    """调用 LLM 对单篇论文进行多标签分类，返回 1~3 个标签列表。"""
    user_msg = f"Title: {title}\n\nAbstract: {abstract[:1500]}\n\nTags:"

    try:
        raw = chat(
            system=SYSTEM_PROMPT,
            user=user_msg,
            model=CLASSIFY_MODEL,
            temperature=0.1,
            max_tokens=1024,
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            tags = json.loads(cleaned)
        except json.JSONDecodeError:
            tags = _repair_truncated_json(cleaned)
            if tags is None:
                raise

        if not isinstance(tags, list):
            raise ValueError(f"Expected list, got {type(tags)}")

        valid: list[str] = []
        for t in tags:
            resolved = _fuzzy_resolve(str(t).strip())
            if resolved and resolved not in valid:
                valid.append(resolved)

        if not valid:
            logger.warning("LLM returned no valid tags from: %r, fallback", raw)
            return ["Core ML"]

        return valid[:3]

    except Exception as e:
        logger.error("Classification failed: %s, fallback to Core ML", e)
        return ["Core ML"]
