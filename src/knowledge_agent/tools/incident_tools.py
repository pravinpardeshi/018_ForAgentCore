"""Strands tools exposed to the Knowledge Agent.

Keep tool surfaces small and typed — the LLM sees these docstrings.
All DB/settings access is lazy so `agentcore dev` and unit tests can
import this module without live AWS/PostgreSQL credentials.
"""

from __future__ import annotations

import logging
from typing import Optional

from strands import tool

from knowledge_agent.config import load_settings

log = logging.getLogger(__name__)


@tool
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


@tool
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
