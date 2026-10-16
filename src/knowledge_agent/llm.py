"""Lazy Bedrock chat-model helper with graceful offline fallback.

All specialist agents go through :func:`invoke_text` / :func:`invoke_json`.
When Bedrock credentials/models are unavailable (unit tests, offline dev),
these raise a RuntimeError that callers catch to fall back to deterministic
heuristics — so nodes never crash for lack of an LLM.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _chat_model():
    from langchain_aws import ChatBedrock

    from knowledge_agent.config import load_settings

    settings = load_settings()
    return ChatBedrock(
        model_id=settings.bedrock_model_id,
        region_name=settings.bedrock_region,
        model_kwargs={
            "max_tokens": settings.bedrock_max_tokens,
            "temperature": settings.bedrock_temperature,
        },
    )


def get_chat_model():
    """Return the cached ChatBedrock model (lazy — no network on init)."""
    return _chat_model()


def reset_chat_model_cache() -> None:
    _chat_model.cache_clear()


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text", block)))
            else:
                text = getattr(block, "text", None)
                parts.append(str(text if text is not None else block))
        return "".join(parts)
    return str(content)


def invoke_text(system: str, user: str) -> str:
    """Single LLM call returning raw text. Raises on any failure."""
    from langchain_core.messages import HumanMessage, SystemMessage

    model = get_chat_model()
    resp = model.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    text = _content_to_text(resp.content).strip()
    if not text:
        raise RuntimeError("LLM returned empty content")
    return text


def invoke_json(system: str, user: str) -> dict:
    """Single LLM call expected to return a JSON object (fences tolerated)."""
    text = invoke_text(system, user)
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    elif cleaned.startswith("```"):
        cleaned = re.sub(r"^```\w*\n?|\n?```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        # Last resort: extract the first {...} block.
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            raise RuntimeError(f"LLM did not return JSON: {e}") from e
        parsed = json.loads(m.group(0))
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM JSON was not an object")
    return parsed
