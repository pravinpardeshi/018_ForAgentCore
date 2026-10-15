"""Unit tests for the tool layer (no AWS/DB needed)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from knowledge_agent.tools import incident_tools


def test_search_returns_payload_shape(monkeypatch):
    fake = [{"incident_number": "INC0000001", "semantic_score": 0.9,
             "keyword_score": 1.0, "combined_score": 0.8,
             "short_description": "x"}]
    monkeypatch.setattr(
        "knowledge_agent.db.search.hybrid_search", lambda **kw: fake
    )
    # hybrid_search is imported lazily inside the tool; patch target module attr
    import knowledge_agent.db.search as s
    monkeypatch.setattr(s, "hybrid_search", lambda **kw: fake)
    out = incident_tools.search_closed_incidents("vpn down")
    assert out["count"] == 1


def test_get_incident_not_found(monkeypatch):
    import knowledge_agent.db.search as s
    monkeypatch.setattr(s, "get_incident", lambda *a, **k: None)
    out = incident_tools.get_incident_details("INC9999999")
    assert out["found"] is False
