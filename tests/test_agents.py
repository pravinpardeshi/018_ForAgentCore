"""Unit tests for the multi-agent pipeline (no AWS/DB needed)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from knowledge_agent.agents import triage as triage_mod
from knowledge_agent.agents import knowledge as knowledge_mod
from knowledge_agent.agents import rca as rca_mod
from knowledge_agent.agents import solution as solution_mod
from knowledge_agent.agents import supervisor as supervisor_mod


FAKE_HITS = [
    {
        "incident_number": "INC0012345",
        "short_description": "VPN error 809 on Windows 11",
        "description_excerpt": "User could not connect to corporate VPN, error 809.",
        "resolution_excerpt": "Re-enabled IKEv2 passthrough; reinstalled profile.",
        "resolution_notes": "Re-enabled IKEv2 passthrough on home router.",
        "root_cause": "IKEv2 blocked by home router firewall",
        "category": "Network", "subcategory": "VPN",
        "assignment_group": "Network Ops", "priority": "3",
        "resolved_at": "2026-08-10T10:00:00+00:00",
        "semantic_score": 0.87, "keyword_score": 2.1, "combined_score": 0.72,
    },
    {
        "incident_number": "INC0012346",
        "short_description": "VPN timeout on macOS",
        "description_excerpt": "VPN drops after 5 minutes.",
        "resolution_excerpt": "Updated AnyConnect client.",
        "resolution_notes": "Updated AnyConnect client to 4.10.",
        "root_cause": "Outdated VPN client",
        "category": "Network",
        "assignment_group": "Network Ops",
        "resolved_at": "2026-08-11T10:00:00+00:00",
        "semantic_score": 0.6, "keyword_score": 1.0, "combined_score": 0.5,
    },
]


# --- Triage ---

def test_triage_heuristic_network():
    out = triage_mod.classify_heuristic("VPN error 809 on Windows 11, cannot connect")
    assert out["category"] == "Network"
    assert out["assignment_group"] == "Network Ops"
    assert out["priority"] == "2"


def test_triage_heuristic_outage_is_p1():
    out = triage_mod.classify_heuristic("Production outage, SAP down for all users")
    assert out["priority"] == "1"
    assert out["urgency"] == "High"


def test_triage_heuristic_unknown():
    out = triage_mod.classify_heuristic("blarg wibble considerably")
    assert out["category"] == "Other"
    assert out["confidence"] <= 0.5


def test_triage_node_writes_slice():
    out = triage_mod.triage_node({"prompt": "Outlook keeps disconnecting"})
    assert out["triage"]["category"] == "Email"


def test_run_triage_offline_flag():
    out = triage_mod.run_triage("printer not working", use_llm=False)
    assert out["category"] == "Hardware"
    assert out["method"] == "heuristic"


# --- Knowledge ---

def test_knowledge_node_uses_tools(monkeypatch):
    import knowledge_agent.db.search as s
    monkeypatch.setattr(s, "hybrid_search", lambda **kw: FAKE_HITS)
    monkeypatch.setattr(s, "get_incident", lambda *a, **k: None)
    out = knowledge_mod.knowledge_node({"prompt": "vpn 809", "top_k": 5, "triage": {}})
    assert len(out["incidents"]) == 2
    assert out["incidents"][0]["incident_number"] == "INC0012345"


# --- RCA ---

def test_rca_heuristic_ranks_by_score():
    out = rca_mod.heuristic_rca({"category": "Network"}, FAKE_HITS)
    assert out["primary_cause"] == "IKEv2 blocked by home router firewall"
    assert out["hypotheses"][0]["evidence"] == ["INC0012345"]


def test_rca_empty_evidence():
    out = rca_mod.run_rca("mystery issue", triage={}, incidents=[], use_llm=False)
    assert out["hypotheses"] == []
    assert out["data_gaps"]


def test_rca_node_writes_slice():
    out = rca_mod.rca_node({"prompt": "vpn", "triage": {}, "incidents": FAKE_HITS})
    assert "primary_cause" in out["rca"]


# --- Solution ---

def test_solution_cites_incidents():
    out = solution_mod.run_solution("vpn 809", triage={"assignment_group": "Network Ops",
                                                       "priority": "2"},
                                    incidents=FAKE_HITS, rca={}, use_llm=False)
    assert out["recommendations"][0]["source_incidents"] == ["INC0012345"]
    assert out["verification_steps"]


def test_solution_empty_evidence_escalates():
    out = solution_mod.run_solution("mystery", triage={"assignment_group": "Service Desk",
                                                       "priority": "3"},
                                    incidents=[], rca={}, use_llm=False)
    assert out["recommendations"] == []
    assert "Service Desk" in out["escalation_guidance"]


# --- Supervisor ---

def test_normalise_mode():
    assert supervisor_mod.normalise_mode("triage") == "triage"
    assert supervisor_mod.normalise_mode("bogus") in ("full", "triage", "knowledge",
                                                      "rca", "solution")


def test_format_answer_modes():
    state = {"prompt": "vpn?", "mode": "triage",
             "triage": {"category": "Network", "priority": "2", "urgency": "High",
                        "assignment_group": "Network Ops", "summary": "VPN broken"}}
    assert "Network Ops" in supervisor_mod.format_answer(state)
    state_empty = {"prompt": "x", "mode": "knowledge", "incidents": []}
    assert "could not find" in supervisor_mod.format_answer(state_empty)
    state_full = {"prompt": "vpn?", "mode": "full", "triage": state["triage"],
                  "incidents": FAKE_HITS,
                  "rca": {"primary_cause": "router firewall"},
                  "solution": {"recommendations": [], "verification_steps": [],
                               "escalation_guidance": "escalate"}}
    text = supervisor_mod.format_answer(state_full)
    assert "[INC0012345]" in text


def test_run_support_triage_offline(monkeypatch):
    monkeypatch.setattr("knowledge_agent.llm.invoke_text",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr("knowledge_agent.llm.invoke_json",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    out = supervisor_mod.run_support("VPN error 809", mode="triage", session_id="t-1")
    assert out["mode"] == "triage"
    assert out["triage"]["category"] == "Network"
    assert out["session_id"] == "t-1"


def test_run_support_knowledge_offline(monkeypatch):
    import knowledge_agent.db.search as s
    monkeypatch.setattr(s, "hybrid_search", lambda **kw: FAKE_HITS)
    monkeypatch.setattr(s, "get_incident", lambda *a, **k: None)
    monkeypatch.setattr("knowledge_agent.llm.invoke_text",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr("knowledge_agent.llm.invoke_json",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    out = supervisor_mod.run_support("vpn down", mode="knowledge", session_id="k-1")
    assert len(out["incidents"]) == 2
    assert "[INC0012345]" in out["result"]


def test_run_support_rca_offline(monkeypatch):
    import knowledge_agent.db.search as s
    monkeypatch.setattr(s, "hybrid_search", lambda **kw: FAKE_HITS)
    monkeypatch.setattr(s, "get_incident", lambda *a, **k: None)
    monkeypatch.setattr("knowledge_agent.llm.invoke_text",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr("knowledge_agent.llm.invoke_json",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    out = supervisor_mod.run_support("vpn 809 fails", mode="rca", session_id="r-1")
    assert "IKEv2" in out["rca"]["primary_cause"]
    assert "root cause" in out["result"].lower()


def test_run_support_solution_offline(monkeypatch):
    import knowledge_agent.db.search as s
    monkeypatch.setattr(s, "hybrid_search", lambda **kw: FAKE_HITS)
    monkeypatch.setattr(s, "get_incident", lambda *a, **k: None)
    monkeypatch.setattr("knowledge_agent.llm.invoke_text",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr("knowledge_agent.llm.invoke_json",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    out = supervisor_mod.run_support("vpn 809 fails", mode="solution", session_id="s-1")
    assert out["solution"]["recommendations"]
    assert "[INC0012345]" in out["result"]


def test_supervisor_graph_compiles_and_routes():
    g = supervisor_mod.build_supervisor_graph()
    assert hasattr(g, "invoke")
    # triage-only path through the compiled graph (mock LLM + no retrieval needed)
    import knowledge_agent.llm as llm_mod
    orig = llm_mod.invoke_text
    llm_mod.invoke_text = lambda *a, **k: "Triage summary answer"
    try:
        res = g.invoke({"messages": [], "prompt": "printer jammed", "mode": "triage"},
                       config={"configurable": {"thread_id": "test-triage"}})
        assert res.get("triage", {}).get("category") == "Hardware"
        assert res.get("final_answer") == "Triage summary answer"
    finally:
        llm_mod.invoke_text = orig


# --- Backward compat ---

def test_run_agent_returns_string(monkeypatch):
    from knowledge_agent import agent_factory
    monkeypatch.setattr("knowledge_agent.llm.invoke_text",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr("knowledge_agent.llm.invoke_json",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    out = agent_factory.run_agent("printer jammed", session_id="c-1", mode="triage")
    assert isinstance(out, str) and "Hardware" in out


def test_main_invoke_forwards_agent(monkeypatch):
    import main
    captured = {}

    def fake_run(prompt, session_id=None, mode=None, top_k=None):
        captured.update(prompt=prompt, session_id=session_id, mode=mode, top_k=top_k)
        return {"result": "ok", "session_id": session_id, "mode": mode,
                "triage": {}, "incidents": [], "rca": {}, "solution": {}}

    monkeypatch.setattr("knowledge_agent.agent_factory.run_agent_with_session", fake_run)
    out = main.invoke({"prompt": "vpn?", "agent": "rca", "session_id": "m-1"})
    assert captured["mode"] == "rca"
    assert out["agent"] == "rca"
    assert out["result"] == "ok"
