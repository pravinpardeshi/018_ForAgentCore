"""AWS Bedrock AgentCore Runtime entrypoint for the ServiceNow Knowledge Agent.

Deploy:
    agentcore configure --entrypoint main.py --non-interactive
    agentcore deploy
    agentcore invoke '{"prompt": "VPN error 809 on Windows 11, how was it fixed before?"}'

Local:
    python main.py   # serves on :8080 with /invocations and /ping

Payload contract:
    {"prompt": "<user question>", "top_k": 5, "session_id": "optional"}
Response:
    {"result": "<agent answer>", "session_id": "..."}
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bedrock_agentcore import BedrockAgentCoreApp  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> dict:
    from knowledge_agent.agent_factory import build_agent

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

    log.info("Invocation: %.120s", prompt)
    agent = build_agent()
    result = agent(prompt.strip())
    text = getattr(result, "message", result)
    if isinstance(text, dict):
        answer = str(text.get("content", text))
    else:
        answer = str(getattr(text, "content", text) if hasattr(text, "content") else text)
    return {"result": answer}


if __name__ == "__main__":
    app.run()
