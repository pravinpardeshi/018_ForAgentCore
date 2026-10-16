"""Shared LangGraph state for the multi-agent support pipeline.

Pipeline: triage -> knowledge -> root-cause -> solution -> final answer.

Each specialist node reads what it needs and writes only its own slice,
so single-agent runs (mode="triage" / "rca" / ...) and the full pipeline
share the same state contract.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from typing_extensions import TypedDict

from langgraph.graph.message import add_messages

AgentMode = Literal["full", "triage", "knowledge", "rca", "solution"]

VALID_MODES: tuple[str, ...] = ("full", "triage", "knowledge", "rca", "solution")


class SupportState(TypedDict, total=False):
    """Graph state threaded through every agent node."""

    # Conversation (MemorySaver-backed per thread_id)
    messages: Annotated[list, add_messages]

    # Request inputs
    prompt: str
    top_k: int
    mode: str

    # Triage agent output
    triage: dict[str, Any]

    # Knowledge agent output (retrieved closed incidents)
    incidents: list[dict[str, Any]]

    # Root-cause agent output
    rca: dict[str, Any]

    # Solution recommender output
    solution: dict[str, Any]

    # Supervisor-synthesised user-facing answer
    final_answer: str
