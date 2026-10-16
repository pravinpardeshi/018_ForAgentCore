"""Root Cause Analysis Agent: diagnose the likely cause from evidence.

LLM synthesis grounded in triage + retrieved incidents, with a deterministic
heuristic fallback (rank hypotheses from incident root-cause fields) so the
node works offline.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _evidence_block(triage: dict, incidents: list[dict]) -> str:
    lines = [
        f"Triage: category={triage.get('category', '?')} "
        f"priority={triage.get('priority', '?')} "
        f"summary={triage.get('summary', '')}"
    ]
    for hit in incidents[:8]:
        lines.append(
            f"[{hit.get('incident_number', '?')}] "
            f"{hit.get('short_description', '')} | "
            f"root_cause={hit.get('root_cause', '') or 'n/a'} | "
            f"resolution={(hit.get('resolution_notes', '') or hit.get('resolution_excerpt', ''))[:300]} | "
            f"score={hit.get('combined_score', hit.get('semantic_score', '?'))}"
        )
    if not incidents:
        lines.append("No closed incidents retrieved.")
    return "\n".join(lines)


def heuristic_rca(triage: dict, incidents: list[dict]) -> dict[str, Any]:
    """Rank root-cause hypotheses from incident fields — no LLM needed."""
    if not incidents:
        return {
            "primary_cause": "Unknown — no relevant closed incidents retrieved.",
            "hypotheses": [],
            "data_gaps": [
                "No matching closed incidents; need error text, CI name, timeframe, "
                "recent changes, and assignment group to diagnose."
            ],
            "confidence": 0.2,
            "method": "heuristic",
        }
    scored = []
    for hit in incidents[:5]:
        cause = (hit.get("root_cause", "") or "").strip() or "Cause documented in resolution notes"
        try:
            conf = float(hit.get("combined_score", hit.get("semantic_score", 0.5)) or 0.5)
        except (TypeError, ValueError):
            conf = 0.5
        scored.append({
            "cause": cause,
            "confidence": round(max(0.0, min(1.0, conf)), 3),
            "evidence": [hit.get("incident_number", "?")],
        })
    scored.sort(key=lambda h: h["confidence"], reverse=True)
    return {
        "primary_cause": scored[0]["cause"],
        "hypotheses": scored,
        "data_gaps": [],
        "confidence": scored[0]["confidence"],
        "method": "heuristic",
    }


def run_rca(
    prompt: str,
    triage: dict | None = None,
    incidents: list[dict] | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Diagnose `prompt` given triage + incidents; heuristic fallback offline."""
    triage = triage or {}
    incidents = incidents or []
    fallback = heuristic_rca(triage, incidents)
    if not use_llm:
        return fallback
    try:
        from knowledge_agent.llm import invoke_json
        from knowledge_agent.prompts import RCA_PROMPT

        data = invoke_json(
            RCA_PROMPT,
            f"User request:\n{prompt}\n\nEvidence:\n{_evidence_block(triage, incidents)}",
        )
        merged = {**fallback, **{k: v for k, v in data.items() if v is not None}}
        merged["method"] = "llm"
        return merged
    except Exception as e:
        log.debug("RCA LLM unavailable, using heuristic: %s", e)
        return fallback


def rca_node(state: dict) -> dict:
    """LangGraph node: reads prompt/triage/incidents, writes `rca`."""
    return {
        "rca": run_rca(
            state.get("prompt", "") or "",
            triage=state.get("triage") or {},
            incidents=state.get("incidents") or [],
        )
    }
