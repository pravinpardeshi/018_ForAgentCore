"""Knowledge Agent node: retrieve closed incidents (deterministic retrieval).

Uses the existing tool functions directly (no LLM round-trip), so results are
identical to the standalone Knowledge ReAct agent's retrieval step. Triage
output is used as an exact-match filter when its confidence is high.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_DETAIL_TOP_N = 2  # enrich the top-N hits with full resolution/root-cause text


def run_knowledge(
    query: str,
    top_k: int = 5,
    category: str | None = None,
    assignment_group: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve + enrich closed incidents for `query`."""
    from knowledge_agent.tools.incident_tools import (
        get_incident_details,
        search_closed_incidents,
    )

    payload = search_closed_incidents(
        query,
        top_k=top_k,
        category=category,
        assignment_group=assignment_group,
    )
    incidents = list(payload.get("incidents", []) or [])
    for hit in incidents[:_DETAIL_TOP_N]:
        number = hit.get("incident_number", "")
        if not number:
            continue
        try:
            detail = get_incident_details(number)
        except Exception as e:  # never fail the pipeline on enrichment
            log.debug("Detail lookup failed for %s: %s", number, e)
            continue
        if detail.get("found"):
            hit["resolution_notes"] = detail.get("resolution_notes", "")
            hit["root_cause"] = detail.get("root_cause", "")
            hit["work_notes_summary"] = detail.get("work_notes_summary", "")
    return incidents


def knowledge_node(state: dict) -> dict:
    """LangGraph node: reads prompt/top_k (+triage filters), writes `incidents`."""
    from knowledge_agent.config import load_settings

    settings = load_settings()
    prompt = state.get("prompt", "") or ""
    top_k = int(state.get("top_k", 0) or settings.default_top_k)

    category: str | None = None
    assignment_group: str | None = None
    triage = state.get("triage") or {}
    if triage.get("category") not in (None, "", "Other") and float(
        triage.get("confidence", 0) or 0
    ) >= 0.6:
        category = triage["category"]
    # Assignment-group filter only when the LLM itself supplied it confidently;
    # the heuristic group is a routing guess, not a retrieval constraint.

    incidents = run_knowledge(prompt, top_k=top_k, category=category,
                              assignment_group=assignment_group)
    return {"incidents": incidents}
