"""Local placeholder test server: serves the static UI and proxies to the agent.

Why a proxy: the UI calls same-origin /api/ask, so there are no CORS issues
and no SSO. The proxy forwards to the agent (local `python main.py` on :8080
today, API Gateway/ALB later) — the UI never changes.

Run:
    python ui/server.py            # UI on http://localhost:3000
    AGENT_URL=http://localhost:8080/invocations UI_PORT=3000 python ui/server.py

Stdlib only. Localhost testing, not for production (no auth, no TLS).
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8080/invocations")
UI_PORT = int(os.environ.get("UI_PORT", "3000"))
FORWARD_TIMEOUT_S = int(os.environ.get("AGENT_TIMEOUT_S", "180"))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=UI_DIR, **kwargs)

    def log_message(self, fmt, *args):  # quieter logs
        print("ui:", fmt % args)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/config":
            return self._json(200, {"agent_url": AGENT_URL})
        if self.path == "/api/health":
            return self._json(200, {"ok": True})
        if self.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/ask":
            return self._json(404, {"error": "unknown endpoint (use /api/ask)"})
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._json(400, {"error": "missing Content-Length"})
        try:
            incoming = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "body must be JSON {prompt, top_k?, session_id?}"})

        prompt = incoming.get("prompt", "")
        if not isinstance(prompt, str) or not prompt.strip():
            return self._json(400, {"error": "prompt must be a non-empty string"})

        forward = {
            "prompt": prompt.strip(),
            "session_id": incoming.get("session_id", ""),
        }
        if isinstance(incoming.get("top_k"), int):
            forward["top_k"] = max(1, min(incoming["top_k"], 20))

        req = urllib.request.Request(
            AGENT_URL,
            data=json.dumps(forward).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=FORWARD_TIMEOUT_S) as resp:
                return self._json(resp.status, json.loads(resp.read() or b"{}"))
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read() or b"{}")
            except (json.JSONDecodeError, ValueError):
                detail = {"error": f"agent returned HTTP {e.code}"}
            return self._json(502, {"error": detail})
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return self._json(502, {
                "error": f"cannot reach agent at {AGENT_URL}: {e}. "
                         "Is `python main.py` running on :8080?"
            })


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", UI_PORT), Handler)
    print(f"Test UI:  http://localhost:{UI_PORT}")
    print(f"Forwarding /api/ask -> {AGENT_URL}  (SSO bypassed, localhost only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
