"""Local test server: hosts the Angular UI and proxies to the agent.

Two ways to run the UI (from repo root):

  Dev (recommended):  cd ui/agent-ui && npm install && npm start
                      # ng serve on http://localhost:4200, /api/ask proxied
                      # straight to the agent (see agent-ui/proxy.conf.json)

  Static host:        cd ui/agent-ui && npm run build
                      python ui/server.py   # hosts ui/dist on http://localhost:3000

Either way, first start the agent in another terminal:  python main.py  (:8080)

Stdlib only. Localhost testing, not for production (no auth, no TLS).
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
DIST_DIR = os.path.join(UI_DIR, "dist", "browser")
STATIC_DIR = DIST_DIR if os.path.isfile(os.path.join(DIST_DIR, "index.html")) else UI_DIR
AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8080/invocations")
UI_PORT = int(os.environ.get("UI_PORT", "3000"))
FORWARD_TIMEOUT_S = int(os.environ.get("AGENT_TIMEOUT_S", "180"))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

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
        if STATIC_DIR == UI_DIR and self.path == "/index.html":
            # Angular app not built yet — point at the dev workflow instead.
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                b"UI not built. Run: cd ui/agent-ui && npm install && "
                b"(npm start  -> http://localhost:4200,  OR  "
                b"npm run build then re-run this server)"
            )
            return
        if self.path.startswith("/api/"):
            return self._json(404, {"error": "unknown endpoint (use /api/ask)"})
        # SPA fallback: unknown paths serve index.html so client routing works.
        local = os.path.join(STATIC_DIR, self.path.lstrip("/").split("?")[0])
        if not os.path.isfile(local):
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
            return self._json(400, {"error": "body must be JSON {prompt, top_k?, session_id?, agent?}"})

        prompt = incoming.get("prompt", "")
        if not isinstance(prompt, str) or not prompt.strip():
            return self._json(400, {"error": "prompt must be a non-empty string"})

        forward = {
            "prompt": prompt.strip(),
            "session_id": incoming.get("session_id", ""),
        }
        if isinstance(incoming.get("top_k"), int):
            forward["top_k"] = max(1, min(incoming["top_k"], 20))
        if isinstance(incoming.get("agent"), str) and incoming["agent"].strip():
            forward["agent"] = incoming["agent"].strip().lower()

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
