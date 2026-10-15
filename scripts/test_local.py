"""Smoke-test the agent locally WITHOUT AWS/PostgreSQL (mocks retrieval)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from unittest.mock import patch

FAKE_HITS = [
    {
        "incident_number": "INC0012345",
        "short_description": "VPN error 809 on Windows 11",
        "description_excerpt": "User could not connect to corporate VPN, error 809.",
        "resolution_excerpt": "Re-enabled IKEv2 passthrough on home router; reinstalled AnyConnect profile.",
        "category": "Network", "subcategory": "VPN",
        "assignment_group": "Network Ops", "priority": "3",
        "resolved_at": "2026-08-10T10:00:00+00:00",
        "semantic_score": 0.87, "keyword_score": 2.1, "combined_score": 0.72,
    }
]

with patch("knowledge_agent.db.search.hybrid_search", return_value=FAKE_HITS):
    # Patch at import site used by the tool
    import knowledge_agent.db.search as s

    s.hybrid_search = lambda **kw: FAKE_HITS
    from knowledge_agent.tools.incident_tools import search_closed_incidents
    
    out = search_closed_incidents("VPN error 809 Windows 11")
    print(out)
    
    assert out["count"] == 1 and out["incidents"][0]["incident_number"] == "INC0012345"
    print("OK: tool layer works without a live DB.")
