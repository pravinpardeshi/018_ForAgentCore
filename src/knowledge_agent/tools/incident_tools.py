"""LangChain tools exposed to the Knowledge Agent (LangGraph ReAct).

Keep tool surfaces small and typed — the LLM sees these descriptions.
All DB/settings access is lazy so imports work without live AWS/PostgreSQL
credentials.

Design note: `search_closed_incidents` / `get_incident_details` are plain
functions (directly callable, unit-testable). The `..._tool` objects below
wrap them as LangChain StructuredTools for the LangGraph agent.
"""

from __future__ import annotations

import logging
from typing import Optional

from knowledge_agent.config import load_settings

log = logging.getLogger(__name__)


def search_closed_incidents(
    query: str,
    top_k: int = 5,
    category: Optional[str] = None,
    assignment_group: Optional[str] = None,
) -> dict:
    """Search CLOSED ServiceNow incidents by semantic + keyword match.

    Use this FIRST for any how-to / troubleshooting / root-cause question.
    Returns ranked incidents with excerpts and scores; call
    get_incident_details for the full resolution text of the best hits.

    Args:
        query: Natural-language problem description (error text, symptoms, CI name).
        top_k: How many incidents to return (1-20, default 5).
        category: Optional exact category filter (e.g. 'Network', 'Database').
        assignment_group: Optional exact assignment group filter.
    """
    from knowledge_agent.db.search import hybrid_search

    settings = load_settings()
    try:
        results = hybrid_search(
            query=query,
            settings=settings,
            top_k=top_k,
            category=category,
            assignment_group=assignment_group,
        )
        return {"query": query, "count": len(results), "incidents": results}
    except Exception as e:  # surface as tool payload, not a crash
        log.exception("search_closed_incidents failed")
        return {"query": query, "count": 0, "incidents": [], "error": str(e)}


def get_incident_details(incident_number: str) -> dict:
    """Fetch the FULL record of one closed incident by number (e.g. 'INC0012345').

    Use after search_closed_incidents to read complete resolution notes,
    root cause, work-notes summary, dates and metadata before answering.
    """
    from knowledge_agent.db.search import get_incident

    settings = load_settings()
    try:
        rec = get_incident(incident_number, settings)
        if rec is None:
            return {
                "incident_number": incident_number.upper().strip(),
                "found": False,
                "message": "No closed incident with that number in the knowledge base.",
            }
        rec["found"] = True
        return rec
    except Exception as e:
        log.exception("get_incident_details failed")
        return {"incident_number": incident_number, "found": False, "error": str(e)}


def _build_langchain_tools():
    """Build LangChain StructuredTool wrappers (imported lazily)."""
    from langchain_core.tools import StructuredTool

    search_tool = StructuredTool.from_function(
        func=search_closed_incidents,
        name="search_closed_incidents",
        description=(
            "Search CLOSED ServiceNow incidents by semantic + keyword match. "
            "Use this FIRST for any how-to / troubleshooting / root-cause question. "
            "Returns ranked incidents with excerpts and scores; call "
            "get_incident_details for the full resolution text of the best hits."
        ),
    )
    details_tool = StructuredTool.from_function(
        func=get_incident_details,
        name="get_incident_details",
        description=(
            "Fetch the FULL record of one closed incident by number "
            "(e.g. 'INC0012345'). Use after search_closed_incidents to read "
            "complete resolution notes, root cause, dates and metadata."
        ),
    )
    return [search_tool, details_tool]


def get_langchain_tools():
    """Return LangChain tool objects for the LangGraph agent."""
    return _build_langchain_tools()


# Backwards-compatible aliases: some code imports the tool objects directly.
def __getattr__(name: str):
    if name in ("search_closed_incidents_tool", "get_incident_details_tool", "INCIDENT_TOOLS"):
        tools = _build_langchain_tools()
        mapping = {
            "search_closed_incidents_tool": tools[0],
            "get_incident_details_tool": tools[1],
            "INCIDENT_TOOLS": tools,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
