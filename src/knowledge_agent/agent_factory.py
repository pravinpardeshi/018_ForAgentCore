"""Build the Strands Knowledge Agent (lazy — safe to import without creds)."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_agent = None


def build_agent():
    """Create (and cache) the Strands Agent with Bedrock model + incident tools."""
    global _agent
    if _agent is not None:
        return _agent

    from strands import Agent
    from strands.models import BedrockModel

    from knowledge_agent.config import load_settings
    from knowledge_agent.prompts import SYSTEM_PROMPT
    from knowledge_agent.tools.incident_tools import (
        get_incident_details,
        search_closed_incidents,
    )

    settings = load_settings()

    model = BedrockModel(
        model_id=settings.bedrock_model_id,
        region_name=settings.bedrock_region,
        max_tokens=settings.bedrock_max_tokens,
        temperature=settings.bedrock_temperature,
    )

    _agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[search_closed_incidents, get_incident_details],
    )
    log.info("Knowledge Agent built with model %s", settings.bedrock_model_id)
    return _agent
