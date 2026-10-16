"""AWS Bedrock AgentCore Runtime entrypoint for the ServiceNow support agents.

Deploy:
    agentcore configure --entrypoint main.py --non-interactive
    agentcore deploy
    agentcore invoke '{"prompt": "VPN error 809 on Windows 11, how was it fixed before?"}'

Local:
    python main.py   # serves on :8080 with /invocations and /ping

Payload contract:
    {"prompt": "<user question>", "top_k": 5, "session_id": "optional",
     "agent": "full|triage|knowledge|rca|solution"}
Response:
    {"result": "<answer>", "session_id": "...", "agent": "<mode>",
     "triage": {...}, "incidents": [...], "rca": {...}, "solution": {...}}

Agents: triage (classify/route), knowledge (closed-incident retrieval),
rca (root-cause analysis), solution (fix recommendations). Default `full`
runs the whole pipeline triage → knowledge → rca → solution.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bedrock_agentcore import BedrockAgentCoreApp  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> dict:
    from knowledge_agent.agent_factory import run_agent_with_session
    from knowledge_agent.agents.supervisor import normalise_mode
    from knowledge_agent.secrets import preload_service_credentials

    # Warm every configured secret once per cold start (cached when warm).
    # Fails fast here instead of mid-answer on first use.
    preload_service_credentials()

    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return {
            "result": "Please provide a 'prompt' string, e.g. "
            '{"prompt": "Outlook keeps disconnecting — known fixes?"}'
        }

    # Optional per-request hint the agent can use; retrieval defaults
    # themselves are env-driven (DEFAULT_TOP_K / MAX_TOP_K).
    top_k = payload.get("top_k")
    if isinstance(top_k, int):
        prompt = f"[Retrieve up to {max(1, min(top_k, 20))} incidents] {prompt}"

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        session_id = f"session-{uuid.uuid4().hex[:12]}"
    else:
        session_id = session_id.strip()

    agent = normalise_mode(payload.get("agent"))

    log.info("Invocation session=%s agent=%s: %.120s", session_id, agent, prompt)
    out = run_agent_with_session(prompt.strip(), session_id=session_id,
                                 mode=agent, top_k=top_k)
    out.setdefault("agent", agent)
    return out


if __name__ == "__main__":
    app.run()
