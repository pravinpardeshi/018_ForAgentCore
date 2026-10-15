/* Placeholder test client. Same-origin calls to /api/ask (no CORS, no SSO). */
"use strict";

const chatEl = document.getElementById("chat");
const promptEl = document.getElementById("prompt");
const sendBtn = document.getElementById("send");
const topkEl = document.getElementById("topk");
const sessionEl = document.getElementById("session");
const statusEl = document.getElementById("agent-status");

function newSession() {
  sessionEl.value = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));
}
newSession();
document.getElementById("newsession").onclick = () => { newSession(); addMsg("agent", "New session started: " + sessionEl.value); };
document.getElementById("clear").onclick = () => { chatEl.innerHTML = ""; };

fetch("/api/config").then((r) => r.json()).then(
  (c) => { statusEl.textContent = "agent: " + c.agent_url; },
  () => { statusEl.textContent = "agent: unknown"; }
);

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function highlightCites(s) {
  return escapeHtml(s).replace(/(\[INC\d+\])/g, '<span class="cite">$1</span>');
}
function addMsg(role, text, isError) {
  const div = document.createElement("div");
  div.className = "msg " + role + (isError ? " error" : "");
  const label = document.createElement("span");
  label.className = "role";
  label.textContent = role === "user" ? "you" : "agent";
  const body = document.createElement("div");
  body.innerHTML = role === "agent" ? highlightCites(text) : escapeHtml(text);
  div.append(label, body);
  chatEl.append(div);
  div.scrollIntoView({ block: "nearest" });
  return div;
}

async function send() {
  const prompt = promptEl.value.trim();
  if (!prompt || sendBtn.disabled) return;
  const top_k = Math.max(1, Math.min(20, parseInt(topkEl.value, 10) || 5));
  addMsg("user", prompt);
  promptEl.value = "";
  sendBtn.disabled = true;
  const typing = addMsg("agent", "Thinking…");
  typing.classList.add("typing");
  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, top_k, session_id: sessionEl.value }),
    });
    const data = await res.json().catch(() => ({}));
    typing.remove();
    if (!res.ok) {
      addMsg("agent", "Error " + res.status + ": " + (data.error || "request failed"), true);
    } else {
      addMsg("agent", data.result || "(empty result)");
      if (data.session_id) sessionEl.value = data.session_id;
    }
  } catch (e) {
    typing.remove();
    addMsg("agent", "Could not reach the test server: " + e.message, true);
  } finally {
    sendBtn.disabled = false;
    promptEl.focus();
  }
}

sendBtn.onclick = send;
promptEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
promptEl.focus();
