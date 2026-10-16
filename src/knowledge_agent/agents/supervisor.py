"""Supervisor: route a request through the specialist pipeline.

Modes (payload `"agent"` / `AGENT_MODE`):
  full       triage → knowledge → rca → solution → final answer (default)
  triage     triage only
  knowledge  knowledge retrieval only (legacy single-agent behaviour)
  rca        knowledge → rca (diagnosis needs evidence)
  solution   knowledge → rca → solution (fixes need diagnosis + evidence)

Single modes still run their upstream dependencies so outputs are grounded,
but only the requested slice is synthesised into the answer.
"""

from __future__ import annotations

import logging
import uuid

from langgraph.graph import END, START, StateGraph

from knowledge_agent.state import VALID_MODES, SupportState

log = logging.getLogger(__name__)

_graph = None
_checkpointer = None


def normalise_mode(mode: object) -> str:
    """Coerce user input to a valid mode; fall back to the configured default."""
    from knowledge_agent.config import load_settings

    candidate = str(mode or "").strip().lower()
    if candidate in VALID_MODES:
        return candidate
    default = load_settings().default_mode
    return default if default in VALID_MODES else "full"


def _entry_route(state: SupportState) -> str:
    mode = state.get("mode", "full")
    if mode in ("full", "triage", "knowledge"):
        return mode
    # rca/solution need retrieval evidence first — but never skip triage's
    # filters silently: triage is cheap, so run it for every mode except
    # legacy knowledge-only (which preserves exact old behaviour).
    return "triage_then_knowledge" if mode in ("rca", "solution") else "full"


def _after_triage(state: SupportState) -> str:
    return "final" if state.get("mode") == "triage" else "knowledge"


def _after_rca(state: SupportState) -> str:
    return "final" if state.get("mode") == "rca" else "solution"


def format_answer(state: SupportState) -> str:
    """Deterministic synthesis — used directly and as LLM fallback."""
    triage = state.get("triage") or {}
    incidents = state.get("incidents") or []
    rca = state.get("rca") or {}
    solution = state.get("solution") or {}
    mode = state.get("mode", "full")

    if mode == "triage":
        return (
            f"Classification: {triage.get('category', '?')} "
            f"(priority {triage.get('priority', '?')}, "
            f"{triage.get('urgency', '?')} urgency) → "
            f"{triage.get('assignment_group', 'Service Desk')}. "
            f"{triage.get('summary', '')}".strip()
        )
    if mode == "knowledge":
        if not incidents:
            return "I could not find a relevant closed incident for this issue."
        lines = ["Related closed incidents:"]
        for hit in incidents:
            lines.append(
                f"- [{hit.get('incident_number', '?')}] "
                f"{hit.get('short_description', '')} "
                f"(resolved {hit.get('resolved_at', 'n/a')})"
            )
        return "\n".join(lines)
    if mode == "rca":
        if not incidents:
            return (
                "Root-cause analysis inconclusive: "
                "I could not find a relevant closed incident for this issue. "
                f"{(rca.get('data_gaps') or [''])[0] if rca.get('data_gaps') else ''}".strip()
            )
        out = [f"Likely root cause: {rca.get('primary_cause', 'unknown')}"]
        for h in (rca.get("hypotheses") or [])[:3]:
            out.append(
                f"- {h.get('cause', '?')} "
                f"(confidence {h.get('confidence', '?')}, "
                f"evidence {', '.join(h.get('evidence', []))})"
            )
        return "\n".join(out)
    if mode == "solution" and not solution.get("recommendations"):
        return str(solution.get("primary_recommendation", "No recommendation available."))

    # full (and solution with evidence): structured report
    parts = []
    summary = triage.get("summary", "")
    if summary:
        parts.append(f"Summary: {summary}")
    parts.append(
        f"Classification: {triage.get('category', '?')} / "
        f"priority {triage.get('priority', '?')} → "
        f"{triage.get('assignment_group', 'Service Desk')}"
    )
    if rca.get("primary_cause"):
        parts.append(f"Likely root cause: {rca['primary_cause']}")
    recs = solution.get("recommendations") or []
    if recs:
        parts.append("Recommended steps:")
        for i, rec in enumerate(recs[:3], 1):
            src = ", ".join(rec.get("source_incidents", []))
            parts.append(f"{i}. {rec.get('title', '')} [{src}]")
            for step in (rec.get("steps") or [])[:6]:
                parts.append(f"   - {step}")
    if incidents:
        parts.append("Related incidents:")
        for hit in incidents[:5]:
            parts.append(
                f"- [{hit.get('incident_number', '?')}] "
                f"{hit.get('short_description', '')} "
                f"({hit.get('assignment_group', '')} "
                f"{hit.get('resolved_at', '')})".rstrip()
            )
    else:
        parts.append("I could not find a relevant closed incident for this issue.")
    if solution.get("verification_steps"):
        parts.append("Verify: " + "; ".join(solution["verification_steps"][:3]))
    if solution.get("escalation_guidance"):
        parts.append(str(solution["escalation_guidance"]))
    return "\n".join(parts)


def final_node(state: SupportState) -> dict:
    """Synthesise specialist outputs (LLM, deterministic fallback)."""
    from langchain_core.messages import AIMessage

    try:
        from knowledge_agent.llm import invoke_text
        from knowledge_agent.prompts import SUPERVISOR_PROMPT

        context = (
            f"Mode: {state.get('mode', 'full')}\n"
            f"Triage: {state.get('triage', {})}\n"
            f"RCA: {state.get('rca', {})}\n"
            f"Solution: {state.get('solution', {})}\n"
            f"Incidents: {str(state.get('incidents', []))[:4000]}"
        )
        answer = invoke_text(
            SUPERVISOR_PROMPT,
            f"User request:\n{state.get('prompt', '')}\n\nSpecialist outputs:\n{context}",
        )
    except Exception as e:
        log.debug("Supervisor LLM unavailable, using template: %s", e)
        answer = format_answer(state)
    return {"final_answer": answer, "messages": [AIMessage(content=answer)]}


def knowledge_entry_node(state: SupportState) -> dict:
    """Legacy knowledge-only entry: retrieval without triage filtering."""
    from knowledge_agent.agents.knowledge import knowledge_node

    return knowledge_node({**state, "triage": {}})


def build_supervisor_graph():
    """Create (and cache) the compiled supervisor graph."""
    global _graph, _checkpointer
    if _graph is not None:
        return _graph

    from langgraph.checkpoint.memory import MemorySaver

    from knowledge_agent.agents.knowledge import knowledge_node
    from knowledge_agent.agents.rca import rca_node
    from knowledge_agent.agents.solution import solution_node
    from knowledge_agent.agents.triage import triage_node

    builder = StateGraph(SupportState)
    builder.add_node("triage", triage_node)
    builder.add_node("triage_then_knowledge", triage_node)
    builder.add_node("knowledge", knowledge_node)
    builder.add_node("knowledge_entry", knowledge_entry_node)
    builder.add_node("rca", rca_node)
    builder.add_node("solution", solution_node)
    builder.add_node("final", final_node)

    builder.add_conditional_edges(START, _entry_route, {
        "full": "triage",
        "triage": "triage",
        "knowledge": "knowledge_entry",
        "triage_then_knowledge": "triage_then_knowledge",
    })
    builder.add_conditional_edges("triage", _after_triage, {
        "final": "final",
        "knowledge": "knowledge",
    })
    builder.add_edge("triage_then_knowledge", "knowledge")
    builder.add_edge("knowledge", "rca")
    builder.add_conditional_edges("rca", _after_rca, {
        "final": "final",
        "solution": "solution",
    })
    builder.add_edge("solution", "final")
    # legacy knowledge-only entry: retrieval without triage filtering.
    builder.add_edge("knowledge_entry", "final")
    builder.add_edge("final", END)

    _checkpointer = MemorySaver()
    _graph = builder.compile(checkpointer=_checkpointer)
    log.info("Support supervisor graph built (triage/knowledge/rca/solution)")
    return _graph


def run_support(
    prompt: str,
    mode: str = "full",
    top_k: int | None = None,
    session_id: str | None = None,
) -> dict:
    """Run the supervisor pipeline; return answer + all specialist slices.

    Single modes run their minimal upstream chain directly (fewer LLM calls);
    `full` runs the compiled graph with conversation memory.
    """
    import copy

    from langchain_core.messages import HumanMessage

    from knowledge_agent.config import load_settings

    settings = load_settings()
    mode = normalise_mode(mode or settings.default_mode)
    thread_id = (session_id or "").strip() or f"session-{uuid.uuid4().hex[:12]}"
    k = top_k if isinstance(top_k, int) else settings.default_top_k

    # Single modes that need downstream evidence run their minimal chain
    # directly (fewer LLM calls than the compiled full graph would make).
    if mode == "triage":
        from knowledge_agent.agents.triage import run_triage

        triage = run_triage(prompt)
        state: SupportState = {"prompt": prompt, "top_k": k, "mode": mode,
                               "triage": triage}
        answer = format_answer(state)
        try:
            from knowledge_agent.llm import invoke_text
            from knowledge_agent.prompts import SUPERVISOR_PROMPT

            answer = invoke_text(
                SUPERVISOR_PROMPT,
                f"User request:\n{prompt}\n\nTriage only mode.\nTriage: {triage}",
            )
        except Exception:
            pass
        return {"result": answer, "session_id": thread_id, "mode": mode,
                "triage": triage, "incidents": [], "rca": {}, "solution": {}}

    if mode == "knowledge":
        from knowledge_agent.agents.knowledge import run_knowledge

        incidents = run_knowledge(prompt, top_k=k)
        state = {"prompt": prompt, "top_k": k, "mode": mode, "incidents": incidents}
        answer = format_answer(state)
        try:
            from knowledge_agent.llm import invoke_text
            from knowledge_agent.prompts import SUPERVISOR_PROMPT

            answer = invoke_text(
                SUPERVISOR_PROMPT,
                f"User request:\n{prompt}\n\nKnowledge-only mode.\n"
                f"Incidents: {str(incidents)[:4000]}",
            )
        except Exception:
            pass
        return {"result": answer, "session_id": thread_id,
                "mode": mode, "triage": {}, "incidents": incidents,
                "rca": {}, "solution": {}}

    if mode in ("rca", "solution"):
        from knowledge_agent.agents.knowledge import run_knowledge
        from knowledge_agent.agents.rca import run_rca
        from knowledge_agent.agents.solution import run_solution
        from knowledge_agent.agents.triage import run_triage

        triage = run_triage(prompt)
        category = (triage.get("category")
                    if triage.get("category") != "Other"
                    and float(triage.get("confidence", 0) or 0) >= 0.6 else None)
        incidents = run_knowledge(prompt, top_k=k, category=category)
        rca = run_rca(prompt, triage=triage, incidents=incidents)
        solution = {}
        if mode == "solution":
            solution = run_solution(prompt, triage=triage,
                                    incidents=incidents, rca=rca)
        state = {"prompt": prompt, "top_k": k, "mode": mode, "triage": triage,
                 "incidents": incidents, "rca": rca, "solution": solution}
        answer = format_answer(state)
        if mode == "solution":
            try:
                from knowledge_agent.llm import invoke_text
                from knowledge_agent.prompts import SUPERVISOR_PROMPT

                answer = invoke_text(
                    SUPERVISOR_PROMPT,
                    f"User request:\n{prompt}\n\nSolution mode.\n"
                    f"Triage: {triage}\nRCA: {rca}\nSolution: {solution}\n"
                    f"Incidents: {str(incidents)[:4000]}",
                )
            except Exception:
                pass
        return {"result": answer, "session_id": thread_id, "mode": mode,
                "triage": triage, "incidents": incidents, "rca": rca,
                "solution": solution}

    # full: run the compiled graph (conversation memory via checkpointer).
    graph = build_supervisor_graph()
    recursion_limit = max(30, settings.max_tool_steps * 4 + 10)
    result = graph.invoke(
        {"messages": [HumanMessage(content=prompt)],
         "prompt": prompt, "top_k": k, "mode": "full"},
        config={"configurable": {"thread_id": thread_id},
                "recursion_limit": recursion_limit},
    )
    answer = result.get("final_answer") or ""
    if not answer:  # graph short-circuit safety net
        answer = format_answer({**copy.deepcopy(result), "mode": "full"})
    return {
        "result": answer,
        "session_id": thread_id,
        "mode": "full",
        "triage": result.get("triage", {}),
        "incidents": result.get("incidents", []),
        "rca": result.get("rca", {}),
        "solution": result.get("solution", {}),
    }
