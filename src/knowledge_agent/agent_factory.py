"""Agent factory: supervisor-led support pipeline (LangGraph).

Specialists (see :mod:`knowledge_agent.agents`):
  triage      – classify + route the request
  knowledge   – retrieve closed ServiceNow incidents (existing tools)
  rca         – root-cause analysis grounded in retrieved incidents
  solution    – solution recommendations grounded in incidents + RCA

Backward compatibility: :func:`build_agent`, :func:`build_graph`,
:func:`run_agent` and :func:`run_agent_with_session` keep their names and
signatures (with an optional `mode`/`agent` argument added).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def build_agent():
    """Create (and cache) the compiled supervisor graph (full pipeline)."""
    from knowledge_agent.agents.supervisor import build_supervisor_graph

    return build_supervisor_graph()


# Alias with LangGraph-native naming.
def build_graph():
    return build_agent()


def build_knowledge_react_agent():
    """Legacy standalone Knowledge agent (ChatBedrock + retrieval tools).

    Uses the current ``langchain.agents.create_agent`` (the non-deprecated
    successor of ``langgraph.prebuilt.create_react_agent``). Kept for direct
    tool-loop use; the pipeline's knowledge step uses deterministic retrieval
    instead. Lazy — safe to import without creds.
    """
    from langchain.agents import create_agent
    from langgraph.checkpoint.memory import MemorySaver

    from knowledge_agent.config import load_settings
    from knowledge_agent.llm import get_chat_model
    from knowledge_agent.prompts import SYSTEM_PROMPT
    from knowledge_agent.tools.incident_tools import get_langchain_tools

    settings = load_settings()
    log.info("Knowledge agent (create_agent) built with model %s", settings.bedrock_model_id)
    return create_agent(
        get_chat_model(),
        get_langchain_tools(),
        system_prompt=SYSTEM_PROMPT,
        checkpointer=MemorySaver(),
    )


def run_agent(
    prompt: str,
    session_id: str | None = None,
    mode: str | None = None,
    **kwargs,
) -> str:
    """Run one prompt through the pipeline; return the final answer text.

    `mode` is one of full|triage|knowledge|rca|solution (default from
    AGENT_MODE, else "full"). `agent=` is accepted as an alias for `mode`.
    """
    from knowledge_agent.agents.supervisor import normalise_mode, run_support

    if mode is None:
        mode = kwargs.get("agent")
    out = run_support(
        prompt,
        mode=normalise_mode(mode) if mode else _default_mode(),
        session_id=session_id,
        top_k=kwargs.get("top_k"),
    )
    return out["result"]


def _default_mode() -> str:
    from knowledge_agent.config import load_settings

    return load_settings().default_mode


def run_agent_with_session(
    prompt: str,
    session_id: str | None = None,
    mode: str | None = None,
    **kwargs,
) -> dict:
    """Run the pipeline and return {"result", "session_id", ...slices}."""
    import uuid

    from knowledge_agent.agents.supervisor import run_support

    thread_id = (session_id or "").strip() or f"session-{uuid.uuid4().hex[:12]}"
    out = run_support(prompt, mode=mode or _default_mode(),
                      session_id=thread_id, top_k=kwargs.get("top_k"))
    return out
