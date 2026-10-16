"""Solution Recommender Agent: turn diagnosis + evidence into fix steps.

LLM synthesis grounded in triage + incidents + RCA, with a deterministic
heuristic fallback (promote each top incident's resolution into a
recommendation) so the node works offline.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def heuristic_solution(
    triage: dict,
    incidents: list[dict],
    rca: dict,
) -> dict[str, Any]:
    """Build recommendations from incident resolutions — no LLM needed."""
    recommendations = []
    for hit in incidents[:5]:
        text = (hit.get("resolution_notes", "") or hit.get("resolution_excerpt", "")).strip()
        if not text:
            continue
        steps = [s.strip("- ").strip() for s in text.split("\n") if s.strip()][:6] or [text[:500]]
        try:
            conf = float(hit.get("combined_score", hit.get("semantic_score", 0.5)) or 0.5)
        except (TypeError, ValueError):
            conf = 0.5
        recommendations.append({
            "title": hit.get("short_description", "Fix from past incident") or "Fix from past incident",
            "steps": steps,
            "source_incidents": [hit.get("incident_number", "?")],
            "confidence": round(max(0.0, min(1.0, conf)), 3),
            "risk": "Follow change control; validate in non-prod first where applicable.",
        })
    if not recommendations:
        return {
            "primary_recommendation": (
                "No grounded fix available — no relevant closed incidents found. "
                "Collect error text, affected CI, timeframe, and recent changes, "
                "then escalate to the triage assignment group."
            ),
            "recommendations": [],
            "verification_steps": ["Reproduce the issue and capture exact error output."],
            "escalation_guidance": (
                f"Escalate to {triage.get('assignment_group', 'Service Desk')} "
                f"(priority {triage.get('priority', '3')})."
            ),
            "confidence": 0.2,
            "method": "heuristic",
        }
    recommendations.sort(key=lambda r: r["confidence"], reverse=True)
    priority = str(triage.get("priority", "3"))
    return {
        "primary_recommendation": recommendations[0]["title"],
        "recommendations": recommendations,
        "verification_steps": [
            "Re-run the failing action and confirm the error is gone.",
            "Check monitoring/logs for recurrence over the next business cycle.",
        ],
        "escalation_guidance": (
            f"If the fix fails, escalate to {triage.get('assignment_group', 'Service Desk')} "
            f"with the attempted steps and incident references "
            f"({', '.join(recommendations[0]['source_incidents'])})."
            if priority in ("1", "2")
            else f"If unresolved, route to {triage.get('assignment_group', 'Service Desk')}."
        ),
        "confidence": recommendations[0]["confidence"],
        "method": "heuristic",
    }


def run_solution(
    prompt: str,
    triage: dict | None = None,
    incidents: list[dict] | None = None,
    rca: dict | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Recommend fixes; heuristic fallback when the LLM is unusable."""
    triage = triage or {}
    incidents = incidents or []
    rca = rca or {}
    fallback = heuristic_solution(triage, incidents, rca)
    if not use_llm:
        return fallback
    try:
        from knowledge_agent.llm import invoke_json
        from knowledge_agent.prompts import SOLUTION_PROMPT

        lines = [
            f"Triage: {triage.get('category', '?')} / P{triage.get('priority', '?')} "
            f"({triage.get('summary', '')})",
            f"Primary cause: {rca.get('primary_cause', 'unknown')}",
        ]
        for hit in incidents[:8]:
            lines.append(
                f"[{hit.get('incident_number', '?')}] "
                f"{hit.get('short_description', '')} → "
                f"{(hit.get('resolution_notes', '') or hit.get('resolution_excerpt', ''))[:400]}"
            )
        if not incidents:
            lines.append("No closed incidents retrieved.")
        data = invoke_json(
            SOLUTION_PROMPT,
            f"User request:\n{prompt}\n\nContext:\n" + "\n".join(lines),
        )
        merged = {**fallback, **{k: v for k, v in data.items() if v is not None}}
        merged["method"] = "llm"
        return merged
    except Exception as e:
        log.debug("Solution LLM unavailable, using heuristic: %s", e)
        return fallback


def solution_node(state: dict) -> dict:
    """LangGraph node: reads prompt/triage/incidents/rca, writes `solution`."""
    return {
        "solution": run_solution(
            state.get("prompt", "") or "",
            triage=state.get("triage") or {},
            incidents=state.get("incidents") or [],
            rca=state.get("rca") or {},
        )
    }
