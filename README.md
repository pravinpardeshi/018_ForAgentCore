<<<<<<< HEAD
# ServiceNow Knowledge Agent on AWS Bedrock AgentCore

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![AWS](https://img.shields.io/badge/AWS-Bedrock_AgentCore-orange)
![Database](https://img.shields.io/badge/PostgreSQL-pgvector-blue)
![License](https://img.shields.io/badge/license-MIT-green)

A production-ready Knowledge Agent that answers IT support questions **grounded exclusively in closed ServiceNow incidents** stored in PostgreSQL + pgvector, deployed as a serverless agent on **AWS Bedrock AgentCore Runtime**.

> **Scope note:** a separate pipeline owns ingestion into PostgreSQL. This repo is the **query-side agent**: retrieval tools, Strands agent, AgentCore entrypoint, and deployment assets.

## Table of Contents

- [How It Works](#how-it-works)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Quickstart (Local)](#quickstart-local)
- [Local Test UI (no SSO)](#local-test-ui-no-sso)
- [Configuration](#configuration)
- [Secrets Manager Setup](#secrets-manager-setup)
- [Usage](#usage)
- [Agent Tools](#agent-tools)
- [Database Schema](#database-schema)
- [Deploy to AWS Bedrock AgentCore](#deploy-to-aws-bedrock-agentcore)
- [External Access via Entra ID SSO](#external-access-via-entra-id-sso)
- [Testing](#testing)
- [Security Notes](#security-notes)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

## How It Works

```text
User
 │  POST /invocations  {"prompt": "VPN error 809, how was it fixed?", "top_k": 5}
 ▼
AgentCore Runtime (main.py, BedrockAgentCoreApp @app.entrypoint)
 │  Strands Agent (Bedrock Claude, SYSTEM_PROMPT in prompts.py)
 ├─▶ search_closed_incidents(query, top_k, filters)
 │     ├─ Bedrock embed query (Titan v2, 1024-d — MUST match ingestion)
 │     └─ PostgreSQL hybrid retrieval: HNSW cosine similarity + tsvector rank
 └─▶ get_incident_details(INC number) → full resolution / root cause / dates
       │
       ▼  grounded answer with [INCxxxxxxx] citations
```

Key design decisions:

- **Grounded by construction.** The system prompt forces tool-first retrieval and `[INCxxxxxxx]` citations; the agent says "no relevant closed incident found" instead of hallucinating.
- **Hybrid retrieval.** Vector similarity blended with Postgres full-text rank, plus exact filters on `category` / `assignment_group`.
- **Warm-start friendly.** Lazy singleton connection pool and cached Bedrock/BedrockModel clients survive microVM reuse.
- **Credential-flexible.** Static password, Secrets Manager, or RDS IAM auth — no secrets baked into images.

## Features

- Strands agent with Bedrock Claude Sonnet, tuned system prompt for support Q&A
- Two focused tools: semantic incident search + full incident detail lookup
- Hybrid pgvector + `tsvector` ranking with configurable weights and thresholds
- Three Postgres auth modes: password, Secrets Manager JSON, RDS IAM token
- AgentCore-compatible entrypoint (`/invocations`, `/ping`) + local dev server on `:8080`
- Reference schema with HNSW/GIN indexes and least-privilege `ka_reader` role
- Offline smoke test, live DB verifier, and unit tests included

## Tech Stack

| Layer | Choice |
|---|---|
| Agent runtime | AWS Bedrock AgentCore Runtime (`bedrock-agentcore`) |
| Agent framework | Strands Agents (`strands-agents`) + BedrockModel |
| LLM | Bedrock Claude (default `us.anthropic.claude-sonnet-4-20250514-v1:0`, configurable) |
| Embeddings | Bedrock `amazon.titan-embed-text-v2` (1024 dims, configurable) |
| Vector DB | PostgreSQL 15+ with `pgvector` (+ `pg_trgm`) |
| DB driver | `psycopg[binary,pool]` v3 with connection pool |
| Packaging | `pyproject.toml` / `requirements.txt`, `Dockerfile` (Python 3.12-slim, non-root, port 8080) |

## Project Structure

```text
.
├── main.py                              # AgentCore entrypoint (@app.entrypoint)
├── requirements.txt / pyproject.toml
├── .env.example                         # all tunable env vars
├── Dockerfile / .dockerignore
├── src/knowledge_agent/
│   ├── config.py                        # env-driven Settings
│   ├── prompts.py                       # grounding system prompt
│   ├── agent_factory.py                 # lazy Strands Agent builder
│   ├── tools/incident_tools.py          # search_closed_incidents, get_incident_details
│   └── db/
│       ├── connection.py                # pooled connections + 3 auth modes
│       ├── embeddings.py                # Bedrock embed with dim guard
│       └── search.py                    # hybrid SQL + thresholding
├── sql/schema.sql                       # table contract + indexes + ka_reader role
├── scripts/
│   ├── verify_db.py                     # live DB + Bedrock check
│   └── test_local.py                    # offline tool smoke test (no creds)
└── tests/test_tools.py                  # unit tests (mocked DB)
```

## Prerequisites

- Python 3.10+
- AWS credentials with Bedrock + AgentCore permissions, and Bedrock model access enabled for your LLM + embedding model
- PostgreSQL 15+ with the `pgvector` extension, already loaded by your ingestion pipeline
- AgentCore CLI: `npm install -g @aws/agentcore`
- (Optional, container path) Docker + an ECR repository

## Quickstart (Local)

```bash
# 1. Install
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# edit .env: DB_HOST, DB_PASSWORD (or DB_SECRET_ARN / DB_USE_IAM_AUTH), regions

# 3. Create schema (once per database; ingestion pipeline owns the data)
psql "$DATABASE_URL" -f sql/schema.sql

# 4. Verify live path (needs AWS + DB access)
python scripts/verify_db.py

# 5. Offline smoke test (no AWS/DB needed)
python scripts/test_local.py

# 6. Serve locally like AgentCore does
python main.py   # :8080, /invocations + /ping
curl -X POST localhost:8080/invocations \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"VPN error 809 on Windows 11, how was it fixed before?"}'
```

## Local Test UI (no SSO)

`ui/` is a throwaway chat console for testing the agent from a browser with **no SSO, no build, no npm**. It calls same-origin `/api/ask`, and the bundled stdlib-only proxy (`ui/server.py`) forwards to the agent — so there are no CORS issues and the UI never changes when you later point it at API Gateway.

### Prerequisites

- Python 3.10+ with `pip install -r requirements.txt` done.
- `.env` configured (DB + Bedrock regions), AWS credentials available — the UI talks to the **real** agent, which needs both.

### Run it (two terminals, repo root in both)

```bash
# Terminal 1 — start the agent (serves :8080)
python main.py
# expect: INFO ... running / startup logs, no traceback
```

```bash
# Terminal 2 — start the test UI (serves :3000)
python ui/server.py
# expect:
#   Test UI:  http://localhost:3000
#   Forwarding /api/ask -> http://localhost:8080/invocations ...
```

Then open **http://localhost:3000** in your browser, type a question (e.g. `VPN error 809 on Windows 11 — how was it fixed before?`), press Enter, and confirm the answer cites `[INCxxxxxxx]`.

### Useful knobs

```bash
AGENT_URL=http://localhost:8080/invocations \  # where /api/ask forwards (default shown)
UI_PORT=3000 \                                 # UI port (default 3000)
AGENT_TIMEOUT_S=180 \                          # max seconds to wait for an answer
python ui/server.py
```

In the page itself: `top_k` sets how many incidents to retrieve (1–20), `session_id` groups follow-ups into one conversation (New session starts a fresh one), Clear wipes the visible chat only.

### If something goes wrong

| What you see | Meaning / fix |
|---|---|
| UI shows `cannot reach agent … Is python main.py running?` | Terminal 1 isn't up — start `python main.py` first and wait for it to listen on :8080 |
| `{'error': 'prompt must be a non-empty string'}` | You sent an empty message — type a question first |
| Agent answers with an AWS/DB error payload | `.env`/credentials or DB connectivity — re-run `python scripts/verify_db.py` |
| `Address already in use` on :3000 or :8080 | A previous server is still running — stop it (`Ctrl+C`) or set `UI_PORT=3001` |

Localhost only — it binds `127.0.0.1`, has no auth and no TLS, so never expose it beyond your machine. Delete or gate it before any shared deployment; real users go through [External Access via Entra ID SSO](#external-access-via-entra-id-sso).

## Configuration

Copy `.env.example` to `.env`. On AgentCore, set these as Runtime environment variables instead.

| Variable | Default | Purpose |
|---|---|---|
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-20250514-v1:0` | Answering LLM |
| `BEDROCK_REGION` | `us-east-1` | LLM region |
| `BEDROCK_MAX_TOKENS` / `BEDROCK_TEMPERATURE` | `2048` / `0.2` | Decoding params |
| `BEDROCK_EMBEDDING_MODEL_ID` | `amazon.titan-embed-text-v2` | **Must match ingestion pipeline** |
| `BEDROCK_EMBEDDING_REGION` / `EMBEDDING_DIMS` | `us-east-1` / `1024` | **Must match ingestion pipeline** |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` | — / `5432` / `knowledge` / `ka_reader` | Connection target |
| `DB_PASSWORD` | — | Direct auth (leave empty if using below) |
| `DB_SECRET_ARN` | — | Secrets Manager JSON `{username,password,host,port,dbname}` |
| `DB_USE_IAM_AUTH` | `false` | `true` → RDS IAM token instead of password |
| `DB_SSLMODE` | `require` | TLS mode |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `1` / `5` | Pool sizing for microVM reuse |
| `DEFAULT_TOP_K` / `MAX_TOP_K` | `5` / `20` | Retrieval limits |
| `MIN_SIMILARITY` | `0.0` | Drop hits below this semantic score |
| `HYBRID_KEYWORD_WEIGHT` | `0.3` | Blend: `0` = pure vector, `1` = pure keyword |
| `STATEMENT_TIMEOUT_MS` | `15000` | Postgres guardrail |
| `KA_MAX_TOOL_STEPS` | `6` | Reserved agent loop budget |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

> Embedding model + dims are a hard contract: query embeddings and stored `embedding vector(N)` must come from the same model, or results are meaningless. `embeddings.py` raises on dim mismatch.

## Secrets Manager Setup

The agent supports three Postgres auth modes (`db/connection.py`): static `DB_PASSWORD`, Secrets Manager JSON via `DB_SECRET_ARN`, or RDS IAM tokens via `DB_USE_IAM_AUTH=true`. For production, use Secrets Manager (or IAM auth) — never bake passwords into the image.

### 1. Create the secret

It stores one JSON document for the least-privilege `ka_reader` role. Missing keys fall back to the matching `DB_*` env vars:

```json
{
  "username": "ka_reader",
  "password": "REDACTED",
  "host": "your-rds-endpoint.amazonaws.com",
  "port": 5432,
  "dbname": "knowledge"
}
```

Console: Secrets Manager → Store a new secret → "Credentials for Amazon RDS database" (or "Other type of secret" with the JSON above) → name e.g. `snow-ka/postgres-reader`.

CLI:

```bash
aws secretsmanager create-secret \
  --name snow-ka/postgres-reader \
  --description "ServiceNow KA Postgres reader credentials" \
  --secret-string '{"username":"ka_reader","password":"REDACTED","host":"YOUR-RDS-ENDPOINT","port":5432,"dbname":"knowledge"}' \
  --region us-east-1
```

Save the returned ARN — that becomes `DB_SECRET_ARN`.

### 2. Grant the AgentCore execution role read access

```json
{
  "Effect": "Allow",
  "Action": "secretsmanager:GetSecretValue",
  "Resource": "arn:aws:secretsmanager:us-east-1:123456789012:secret:snow-ka/postgres-reader-AbCdEf"
}
```

If the secret uses a customer-managed KMS key, also grant `kms:Decrypt` on that key. Test with `aws secretsmanager get-secret-value --secret-id <ARN>` from an identity with the same policy.

### 3. Wire it into the Runtime

Set these as AgentCore Runtime environment variables (unset `DB_PASSWORD`):

| Env var | Value |
|---|---|
| `DB_SECRET_ARN` | Full secret ARN from step 1 |
| `DB_HOST` | Same host as in the secret (currently still required as a fallback guard) |
| `DB_SSLMODE` | `require` |

Two current-code caveats:

- The Secrets Manager client is built with `BEDROCK_REGION`, so keep the secret in the same region as `BEDROCK_REGION`.
- `get_pool()` requires `DB_HOST` to be set even when the secret contains `host` — set both to the same value.

### 4. Rotation

- Manual: update the secret value. New Runtime sessions read the fresh value on pool creation; redeploy to force immediate pickup.
- Automatic: enable Secrets Manager rotation (Lambda for RDS) on the `ka_reader` password. The agent always fetches the current value when opening a pool, so it is rotation-safe across session churn.

For local dev, a plain `.env` with `DB_PASSWORD` is simplest; only use `DB_SECRET_ARN` locally if your IAM identity has `GetSecretValue` permission.

## Usage

**Request:**

```json
{ "prompt": "Outlook keeps disconnecting — what fixed it before?", "top_k": 5 }
```

**Response:**

```json
{ "result": "Summary...\nResolution steps...\nRelated: [INC0012345] ...\nCaveats..." }
```

Example invocations:

```bash
# Local server
curl -X POST localhost:8080/invocations \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"SAP basis transport RC=8 — known root causes?"}'

# Deployed Runtime
agentcore invoke '{"prompt":"VPN error 809 on Windows 11, how was it fixed before?"}'
```

Behaviour contract: searches closed incidents first, cites every factual claim as `[INCxxxxxxx]`, fetches full details for top hits before answering, and explicitly says when nothing relevant is found (suggesting useful follow-up info like error text, CI, or assignment group).

## Agent Tools

Tools are defined in `src/knowledge_agent/tools/incident_tools.py` with Strands `@tool`. The LLM sees the docstrings, so keep them precise.

| Tool | Signature | When the agent uses it |
|---|---|---|
| `search_closed_incidents` | `(query: str, top_k: int = 5, category?: str, assignment_group?: str)` → `{query, count, incidents[]}` | First step for any how-to / troubleshooting / root-cause question. Returns excerpts + `semantic_score`, `keyword_score`, `combined_score`. |
| `get_incident_details` | `(incident_number: str)` → full record or `{found: false}` | After search, to read complete `resolution_notes`, `root_cause`, dates, and `metadata` before answering. |

Errors surface as tool payloads (`{..., "error": "..."}`), never as agent crashes.

## Database Schema

`sql/schema.sql` is the contract your ingestion pipeline writes to and this agent reads from:

- Table `snow_closed_incidents`: numbers, short/description, `resolution_notes`, `root_cause`, `work_notes_summary`, `category`/`subcategory`, `assignment_group`, `resolved_by`, `priority`, `state` (agent filters `Closed`), `opened_at`/`resolved_at`, generated `search_text` + `search_tsv`, `embedding vector(1024)`, `metadata JSONB`.
- Indexes: HNSW cosine on `embedding`, GIN on `search_tsv`, trigram on `short_description`, btree on `category`, `assignment_group`, `state`, `resolved_at`.
- Least-privilege `ka_reader` role with `SELECT`-only grant.

Changing embedding models? Update `vector(N)` dims in the schema **and** `EMBEDDING_DIMS` together, then re-embed the corpus.

## Deploy to AWS Bedrock AgentCore

### Option A — Direct code deploy (recommended)

```bash
agentcore configure --entrypoint main.py --non-interactive
agentcore deploy
agentcore invoke '{"prompt":"Outlook keeps disconnecting — known fixes?"}'
```

### Option B — Container image

```bash
docker build -t snow-ka .
# tag + push to ECR, then point the AgentCore Runtime at the image
```

The `Dockerfile` already satisfies Runtime requirements (non-root `bedrock_agentcore` user, port `8080`). Set DB/Bedrock env vars on the Runtime; grant network access (security groups / VPC) from the Runtime to RDS/Aurora.

### Option C — Deploy scripts (Windows / macOS / Linux)

`scripts/Publish-ToAgentCore.ps1` (PowerShell) and `scripts/publish-to-agentcore.sh` (bash) automate the upload — direct code deploy by default, ECR build/push in Container mode — including prerequisite checks, AWS identity verification, and a post-deploy smoke test:

```powershell
# Windows — direct code deploy (no Docker needed):
.\scripts\Publish-ToAgentCore.ps1

# Windows — build + push the container image to ECR:
.\scripts\Publish-ToAgentCore.ps1 -Mode Container -EcrRepoName snow-ka -Region us-east-1

# Windows — with an AWS profile and no smoke test:
.\scripts\Publish-ToAgentCore.ps1 -AwsProfile my-sso-profile -SkipSmokeTest
```

```bash
# macOS / Linux — direct code deploy (no Docker needed):
./scripts/publish-to-agentcore.sh

# macOS / Linux — build + push the container image to ECR:
./scripts/publish-to-agentcore.sh --mode container --ecr-repo snow-ka --region us-east-1

# macOS / Linux — with an AWS profile and no smoke test:
./scripts/publish-to-agentcore.sh --profile my-sso-profile --skip-smoke-test
```

If Windows blocks the script, allow it for the current session only: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`. Full parameter docs: `Get-Help .\scripts\Publish-ToAgentCore.ps1 -Full` or `./scripts/publish-to-agentcore.sh --help`.

### Production checklist

- [ ] Embeddings model + dims match ingestion
- [ ] Secrets via Secrets Manager or IAM auth (no passwords in image/env snapshots)
- [ ] `ka_reader` password rotated; TLS `require`d; SGs scoped to Runtime
- [ ] HNSW index built **after** bulk load; `ANALYZE` run
- [ ] `MIN_SIMILARITY` / `HYBRID_KEYWORD_WEIGHT` tuned on real queries
- [ ] CloudWatch logs + AgentCore observability enabled

## External Access via Entra ID SSO

The Runtime's `InvokeAgentRuntime` API uses IAM SigV4 by default, which browsers can't safely sign (it would require AWS credentials in frontend JS, and the endpoint isn't a CORS-enabled public API). The supported pattern is a **backend proxy**: your locally hosted UI logs users in with enterprise SSO (Entra ID), calls your backend with the user's ID token, and the backend — which holds AWS credentials — invokes the agent.

```text
UI (MSAL.js → Entra ID login, PKCE)
 │  POST /ask  Authorization: Bearer <Entra ID token>
 │  {"prompt": "...", "session_id": "..."}
 ▼
Backend (API Gateway + Lambda, or any service)
 │  1. Validate JWT: iss / aud / exp against Entra JWKS
 │  2. Take user identity from validated sub/oid claim (never client input)
 │  3. InvokeAgentRuntime via boto3 (IAM SigV4, backend role)
 ▼
AgentCore Runtime → Strands agent → grounded answer
```

### 1. Entra ID app registration (your Entra admin)

- App registrations → New registration → Accounts in this organizational directory only.
- Authentication → Add platform → **Single-page application (SPA)**: add redirect URIs for local dev (e.g. `http://localhost:3000`) and the deployed UI origin. Public client, auth-code + PKCE — no client secret for the SPA.
- Record **Application (client) ID** and **Directory (tenant) ID**.
- The backend accepts tokens with `iss = https://login.microsoftonline.com/{tenant-id}/v2.0` and `aud = {client-id}`.

### 2. Frontend (MSAL.js)

```bash
npm install @azure/msal-browser
```

```js
import { PublicClientApplication } from "@azure/msal-browser";

const msal = new PublicClientApplication({
  auth: {
    clientId: "<app-client-id>",
    authority: "https://login.microsoftonline.com/<tenant-id>",
  },
});

await msal.loginPopup({ scopes: ["openid", "profile"] });
const account = msal.getAllAccounts()[0];
const { idToken } = await msal.acquireTokenSilent({
  scopes: ["openid", "profile"],
  account,
});

const res = await fetch("https://your-api.execute-api.us-east-1.amazonaws.com/ask", {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${idToken}` },
  body: JSON.stringify({ prompt: userInput, session_id: conversationId }),
});
const { result } = await res.json();
```

Use one `session_id` (UUID) per conversation and reuse it for follow-ups. Handle `InteractionRequiredAuthError` by falling back to `acquireTokenPopup`, and re-log-in on 401s.

### 3. Backend proxy (validates Entra token, calls AgentCore)

Lambda + HTTP API Gateway (with CORS restricted to your UI origin) is the simplest host. The handler below reads the full streaming body for simplicity; relay chunks (SSE/WebSocket) only if you need typewriter UX.

```python
import boto3, json, os

agentcore = boto3.client("bedrock-agentcore", region_name=os.environ["AWS_REGION"])
RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]
# Validate the Entra ID token first (standard OIDC: fetch tenant JWKS,
# verify signature, then check iss/aud/exp). Any OIDC library works.
# Take the caller identity from the VALIDATED sub/oid claim only.

def handler(event, context):
    claims = event["requestContext"]["authorizer"]["jwt"]["claims"]  # via JWT authorizer, or validate manually
    user_id = claims["oid"]  # or "sub" — stable per user, scoped to this app+tenant
    body = json.loads(event.get("body") or "{}")
    session_id = body.get("session_id") or f"{user_id}-{context.aws_request_id}"

    resp = agentcore.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        contentType="application/json",
        accept="application/json",
        payload=json.dumps({"prompt": body.get("prompt", "")}).encode(),
    )
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "https://your-ui-origin"},
        "body": json.dumps({"result": resp["response"].read().decode(),
                            "session_id": session_id}),
    }
```

Backend env: `AGENT_RUNTIME_ARN`, `AWS_REGION`. Backend role needs only `bedrock-agentcore:InvokeAgentRuntime` scoped to the Runtime ARN. Never forward browser input into the `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` header — that requires the separate `InvokeAgentRuntimeForUser` permission and is an impersonation primitive if fed from client input.

### Alternative: direct browser → Runtime with JWT (no backend)

Possible but heavier: create a Runtime version with a **JWT inbound authorizer** (discovery URL `https://login.microsoftonline.com/{tenant-id}/v2.0/.well-known/openid-configuration`, your app's client ID in `allowedClients`), then call the data-plane HTTPS invocations endpoint with `Authorization: Bearer <Entra token>`. A Runtime supports IAM **or** JWT inbound auth, not both. Prefer the backend proxy unless you specifically want to eliminate the middle tier.

### SSO troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `AADSTS50011` redirect mismatch | Redirect URI in code not registered in the Entra app → add exact local + deployed origins as SPA URIs |
| Backend rejects valid token | `aud`/`iss` mismatch (wrong client/tenant ID) or clock skew → align IDs, check `exp`/`nbf` |
| 401 only after ~1h | Token expired; silent renewal failed (e.g. Conditional Access) → catch `InteractionRequiredAuthError`, re-prompt login |

## Testing

```bash
python scripts/test_local.py   # offline tool smoke test, no creds
python scripts/verify_db.py    # live: Bedrock embed + one hybrid search
pytest tests/ -v               # unit tests (DB mocked)
```

## Security Notes

- Never commit `.env` or bake credentials into the Docker image.
- Prefer `DB_SECRET_ARN` or `DB_USE_IAM_AUTH=true` in production (see [Secrets Manager Setup](#secrets-manager-setup)).
- The agent prompt forbids revealing SQL, connection strings, or embedding internals — but still scope DB grants to `SELECT` on the incidents table only.
- `statement_timeout` and pool caps bound worst-case DB load from the Runtime.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `DB_HOST is not set` | `.env` not loaded or Runtime env vars missing (also required alongside `DB_SECRET_ARN`) |
| `AccessDeniedException` on `GetSecretValue` | AgentCore execution role lacks `secretsmanager:GetSecretValue` (or `kms:Decrypt` for CMK secrets) |
| Secret found but wrong host/creds | Secret JSON keys (`host`, `username`, …) stale or in a different region than `BEDROCK_REGION` |
| `Embedding dim mismatch` | Query model differs from ingestion model — align `BEDROCK_EMBEDDING_MODEL_ID` / `EMBEDDING_DIMS` and re-embed |
| Empty results, low scores | Corpus not embedded, wrong table, or `MIN_SIMILARITY` too high — run `verify_db.py`, check `embedding IS NOT NULL` counts |
| Slow search | Missing HNSW/GIN indexes or cold pool — build indexes post-load, raise `DB_POOL_MAX` modestly |
| `agentcore invoke` auth errors | AWS creds/region or Bedrock model access not enabled in that region |

## Roadmap

- [ ] Session memory (AgentCore Memory) for multi-turn follow-ups
- [ ] Feedback loop: mark answers helpful → boost incident ranking
- [ ] Category auto-faceting + assignment-group routing suggestions
- [ ] Evaluation harness over labelled Q/A pairs with citation precision

## Contributing

1. Fork and create a feature branch.
2. `pip install -r requirements.txt` (plus `pytest`, `ruff` for dev).
3. Keep tool docstrings accurate — they are the agent's interface.
4. Add/extend tests under `tests/`; run `pytest tests/ -v` and `ruff check .`.
5. Open a PR describing behaviour change + verification steps.

## License

MIT — see `LICENSE` (add one if publishing). Replace this line with your chosen license file.
=======
# 018_ForAgentCore
AWS AgentCore Setup
>>>>>>> 74982ce68962806af538d025314250a9f2c39111
