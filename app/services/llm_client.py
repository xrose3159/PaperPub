"""多模型 LLM Router — 支持普通对话和 Function Calling (Tool Use)。

所有模型通过 OpenAI-compatible API 统一调用，支持 OneAPI/NewAPI 等代理网关。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from openai import OpenAI
from openai.types.chat import ChatCompletion

from app.core.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

LLM_TIMEOUT = 120  # 单次 LLM 请求最大等待秒数

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ModelEndpoint:
    api_key: str
    base_url: str


MODEL_OVERRIDES: dict[str, _ModelEndpoint] = {
    # "gpt-4o": _ModelEndpoint(api_key="sk-xxx", base_url="https://api.openai.com/v1"),
}

_clients: dict[str, OpenAI] = {}

FIXED_TEMPERATURE_MODELS: dict[str, float] = {
    "kimi-k2.5": 1.0,
}


def _resolve_temperature(model: str, requested: float) -> float:
    for pattern, fixed in FIXED_TEMPERATURE_MODELS.items():
        if pattern in model.lower():
            return fixed
    return requested


def _get_client(model_name: str | None = None) -> tuple[OpenAI, str]:
    """根据 model_name 返回 (client, resolved_model)。"""
    resolved = model_name or OPENAI_MODEL

    override = MODEL_OVERRIDES.get(resolved)
    api_key = override.api_key if override else OPENAI_API_KEY
    base_url = override.base_url if override else OPENAI_BASE_URL

    cache_key = f"{base_url}||{api_key}"
    if cache_key not in _clients:
        _clients[cache_key] = OpenAI(
            api_key=api_key, base_url=base_url, timeout=LLM_TIMEOUT,
        )
    return _clients[cache_key], resolved


def chat(
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> str:
    """发送一次 Chat Completion 请求并返回纯文本。"""
    client, resolved_model = _get_client(model)
    logger.debug("LLM chat -> model=%s", resolved_model)

    temp = _resolve_temperature(resolved_model, temperature)
    resp = client.chat.completions.create(
        model=resolved_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temp,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def chat_with_vision(
    system: str,
    user_text: str,
    images_base64: list[str],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 64,
) -> str:
    """发送带图片的多模态 Chat Completion 请求，返回纯文本。"""
    client, resolved_model = _get_client(model)
    logger.debug("LLM vision -> model=%s, images=%d", resolved_model, len(images_base64))

    content: list[dict] = [{"type": "text", "text": user_text}]
    for b64 in images_base64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
        })

    temp = _resolve_temperature(resolved_model, temperature)
    resp = client.chat.completions.create(
        model=resolved_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        temperature=temp,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def chat_with_tools(
    messages: list[dict],
    tools: list[dict],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> ChatCompletion:
    """发送带 Tool Calling 的 Chat Completion 请求，返回完整 Response。

    调用方需自行处理 tool_calls 并将结果追加到 messages 中继续对话。
    """
    client, resolved_model = _get_client(model)
    logger.debug("LLM chat_with_tools -> model=%s, tools=%d", resolved_model, len(tools))

    temp = _resolve_temperature(resolved_model, temperature)
    kwargs: dict = dict(
        model=resolved_model,
        messages=messages,
        temperature=temp,
        max_tokens=max_tokens,
    )
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    return client.chat.completions.create(**kwargs)
