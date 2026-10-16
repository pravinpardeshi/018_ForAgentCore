"""Triage Agent: classify an incoming request (category, priority, routing).

Runs as an LLM call with a deterministic keyword heuristic as fallback,
so it works offline (tests, no-creds dev) and improves with Bedrock live.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

# category -> keywords that signal it
_CATEGORY_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Network", ("vpn", "error 809", "ikev2", "anyconnect", "dns", "wifi", "wi-fi",
                 "router", "switch", "lan", "wan", "firewall", "packet loss")),
    ("Email", ("outlook", "exchange", "owa", "mailbox", "pst", "imap", "smtp",
               "mail flow", "spam", "phishing mailbox")),
    ("Database", ("sap", "basis", "transport", "sql", "oracle", "deadlock",
                  "tablespace", "query slow", "dbcc")),
    ("Access", ("password", "sso", "single sign", "mfa", "entra", "login",
                "locked out", "permission", "access denied", "vpn token")),
    ("Hardware", ("laptop", "printer", "disk", "blue screen", "bsod", "keyboard",
                  "monitor", "battery", "cpu fan")),
    ("Security", ("malware", "ransomware", "phishing", "breach", "vulnerability",
                  "edr", "crowdstrike", "antivirus")),
    ("Software", ("install", "upgrade", "patch", "crash", "not responding",
                  "version", "license", "teams", "zoom", "sap gui")),
)

_CATEGORY_GROUP: dict[str, str] = {
    "Network": "Network Ops",
    "Email": "Messaging",
    "Database": "DBA",
    "Access": "Identity & Access",
    "Hardware": "Desktop Support",
    "Security": "Security Ops",
    "Software": "App Support",
    "Other": "Service Desk",
}

_P1 = ("outage", "production down", "down for all", "breach", "ransomware",
       "data loss", "sev1", "critical")
_P2 = ("blocked", "cannot work", "fails", "error", "down", "urgent")


def classify_heuristic(text: str) -> dict[str, Any]:
    """Deterministic keyword classification — no network, fully testable."""
    lowered = (text or "").lower()
    best_category = "Other"
    best_hits: list[str] = []
    for category, keywords in _CATEGORY_SIGNALS:
        hits = [k for k in keywords if k in lowered]
        if len(hits) > len(best_hits):
            best_category, best_hits = category, hits
    if any(s in lowered for s in _P1):
        priority, urgency = "1", "High"
    elif any(s in lowered for s in _P2):
        priority, urgency = "2", "High"
    elif re.search(r"\b(how|how-to|request|please add|access request)\b", lowered):
        priority, urgency = "4", "Low"
    elif best_category == "Other":
        priority, urgency = "3", "Medium"
    else:
        priority, urgency = "3", "Medium"

    sentences = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    summary = (sentences[0][:160] if sentences and sentences[0]
               else "IT support request") if (text or "").strip() else "Empty request"
    return {
        "category": best_category,
        "subcategory": "",
        "priority": priority,
        "urgency": urgency,
        "assignment_group": _CATEGORY_GROUP[best_category],
        "summary": summary,
        "confidence": 0.8 if best_hits else 0.4,
        "signals": best_hits[:5],
        "method": "heuristic",
    }


def _refine_with_llm(text: str, fallback: dict[str, Any]) -> dict[str, Any]:
    from knowledge_agent.llm import invoke_json
    from knowledge_agent.prompts import TRIAGE_PROMPT

    data = invoke_json(TRIAGE_PROMPT, f"Request:\n{text}")
    merged = {**fallback, **{k: v for k, v in data.items() if v is not None}}
    merged["method"] = "llm"
    try:
        merged["confidence"] = float(merged.get("confidence", 0.5))
    except (TypeError, ValueError):
        merged["confidence"] = 0.5
    return merged


def run_triage(text: str, use_llm: bool = True) -> dict[str, Any]:
    """Classify `text`; falls back to the heuristic when the LLM is unusable."""
    fallback = classify_heuristic(text)
    if not use_llm or not (text or "").strip():
        return fallback
    try:
        return _refine_with_llm(text, fallback)
    except Exception as e:  # offline / no creds / bad payload → heuristic
        log.debug("Triage LLM unavailable, using heuristic: %s", e)
        return fallback


def triage_node(state: dict) -> dict:
    """LangGraph node: reads prompt, writes `triage`."""
    prompt = state.get("prompt", "") or ""
    return {"triage": run_triage(prompt)}
