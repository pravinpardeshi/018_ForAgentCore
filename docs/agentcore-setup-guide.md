# AWS Bedrock AgentCore — Enable & Configure: The Go-To Guide

> **Purpose:** one document for enabling AgentCore in an AWS account, deploying this Knowledge Agent, operating it, and fixing it when it breaks. Commands target the **AgentCore CLI** (`@aws/agentcore`, npm) + **AWS CLI v2**.
>
> **Freshness note (Oct 2026):** the CLI and docs evolve fast. When in doubt, `agentcore <command> --help` and the official Developer Guide win over this file. Anything marked *(verify)* means "confirm in current docs before relying on it".

---

## Table of Contents

1. [Mental model (60 seconds)](#1-mental-model-60-seconds)
2. [One-time AWS enablement](#2-one-time-aws-enablement)
3. [Install the tooling](#3-install-the-tooling)
4. [IAM: your permissions + the execution role](#4-iam-your-permissions--the-execution-role)
5. [Bedrock model access](#5-bedrock-model-access)
6. [Project setup (this repo)](#6-project-setup-this-repo)
7. [Deploy: what happens and how](#7-deploy-what-happens-and-how)
8. [Invoke, sessions, streaming](#8-invoke-sessions-streaming)
9. [Networking: reaching PostgreSQL](#9-networking-reaching-postgresql)
10. [Auth for outside callers (IAM vs JWT)](#10-auth-for-outside-callers-iam-vs-jwt)
11. [Environment variables & secrets on the Runtime](#11-environment-variables--secrets-on-the-runtime)
12. [Observability: logs, traces, metrics](#12-observability-logs-traces-metrics)
13. [Update, version, and clean up](#13-update-version-and-clean-up)
14. [Troubleshooting bible](#14-troubleshooting-bible)
15. [Command cheat sheet](#15-command-cheat-sheet)
16. [Official references](#16-official-references)

---

## 1. Mental model (60 seconds)

AgentCore is serverless hosting for your agent code plus composable services:

| Piece | What it does for this project |
|---|---|
| **Runtime** | Runs `main.py` in isolated microVMs; exposes `POST /invocations` + `GET /ping`. This is where the Knowledge Agent lives. |
| **Identity** | Inbound auth (who may invoke: IAM SigV4 or JWT) + outbound auth (what the agent may call). |
| **Gateway** | Turns REST/Lambda/MCP targets into agent tools. (Not used by this repo today.) |
| **Memory** | Persistent conversation/context store. (Roadmap item here.) |
| **Observability** | CloudWatch logs, traces, metrics for every invocation. |
| **Code Interpreter / Browser** | Sandboxed execution and web automation. (Not used here.) |

Your deploy loop is always: **code → `agentcore deploy` → `agentcore invoke` → `agentcore logs`**.

---

## 2. One-time AWS enablement

### 2.1 Account & region

- You need an AWS account with admin (or delegated) rights for the first setup; daily deploys need far less (see §4).
- Pick **one region** and stick to it for Runtime + Bedrock models + Secrets Manager + RDS. Region support expands over time — confirm yours in the docs; `us-east-1` is the safest default. Our repo defaults (`BEDROCK_REGION`, `DB_*`) assume a single region.
- The Secrets Manager client in this repo is built with `BEDROCK_REGION`, so the DB secret **must live in that same region**.

### 2.2 Enable Bedrock in the account

1. Console → **Amazon Bedrock** → confirm the service is available in your region.
2. **Model access:** Bedrock → *Model access* → enable the models you need:
   - Answering LLM, e.g. `us.anthropic.claude-sonnet-4-20250514-v1:0` (Anthropic may require a use-case form on first enablement).
   - Embedding model `amazon.titan-embed-text-v2` (usually one click).
   - Without this, everything deploys fine and then fails at invoke time with an access error (§14, symptom 5).
3. Note your **account ID**: `aws sts get-caller-identity --query Account --output text`.

### 2.3 CDK bootstrap (first deploy)

`agentcore deploy` provisions infrastructure **via AWS CDK under the hood**. The first deploy in an account+region bootstraps the CDK toolkit stack (a CloudFormation stack named `CDKToolkit`). That requires broad permissions once; subsequent deploys are narrower and faster. If your org restricts this, ask your platform team to run `cdk bootstrap aws://ACCOUNT/REGION` first.

---

## 3. Install the tooling

| Tool | Install | Verify |
|---|---|---|
| Node.js 20+ (CLI runtime) | https://nodejs.org | `node --version` |
| AgentCore CLI | `npm install -g @aws/agentcore` | `agentcore --version` |
| AWS CLI v2 | https://aws.amazon.com/cli/ | `aws --version` |
| Python 3.10+ (agent code) | https://www.python.org/downloads/ | `python --version` |
| Docker (container path only) | https://www.docker.com/products/docker-desktop/ | `docker info` |

Configure AWS credentials **one** of these ways (first match wins in most tools):

```bash
aws configure            # interactive: key, secret, region, format
# or
export AWS_PROFILE=my-sso-profile   # named profile (SSO-friendly, recommended)
# or
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_REGION=us-east-1
```

Sanity check before anything else:

```bash
aws sts get-caller-identity   # must print your UserId/Account/Arn
```

Our deploy scripts (`scripts/publish-to-agentcore.sh`, `scripts/Publish-ToAgentCore.ps1`) automate these checks.

---

## 4. IAM: your permissions + the execution role

There are **two** identities to get right. Confusing them causes most "AccessDenied" pain.

### 4.1 YOUR identity (the deployer/invoker)

What you need, by task:

| Task | Minimum IAM actions |
|---|---|
| Deploy via CLI (dev) | `BedrockAgentCoreFullAccess` managed policy is the documented shortcut; CLI-created policies are intentionally broad and **dev-only** |
| Invoke the agent | `bedrock-agentcore:InvokeAgentRuntime` scoped to the Runtime ARN |
| Invoke with user-id delegation | additionally `bedrock-agentcore:InvokeAgentRuntimeForUser` (avoid unless you need it; explicitly `Deny` it where unused) |
| Read logs/traces | CloudWatch `logs:*` / observability read on the Runtime's log group |
| Production deploys | Custom least-privilege policy copied from the managed one, resources scoped to your Runtime ARNs |

Example least-privilege invoker policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "InvokeKnowledgeAgent",
    "Effect": "Allow",
    "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
    "Resource": ["arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/*"]
  }]
}
```

Tighten `Resource` to the exact Runtime ARN once deployed (find it via `agentcore status --json`).

### 4.2 The EXECUTION role (what the Runtime runs as)

AgentCore assumes an **execution role** to run your agent: pull the image/code, write logs, call Bedrock models, read Secrets Manager, open DB connections. Key rules:

- The console/CLI can **auto-create** it (default names look like `service-role/AmazonBedrockAgentCoreRuntimeDefaultServiceRole-*` with policy `AmazonBedrockAgentCoreRuntimeExecutionPolicy_*`). Auto-creation needs `iam:CreateRole` / `iam:CreatePolicy` / attach on those name patterns.
- The execution role must have **equal or fewer privileges than the principals who can invoke it** (no privilege escalation path).
- It needs, at minimum for this repo: Bedrock `InvokeModel` (LLM + embeddings), `secretsmanager:GetSecretValue` on the DB secret (+ `kms:Decrypt` for CMK secrets), CloudWatch log write, and network path to RDS.
- Scope IAM auth notes: if you use `DB_USE_IAM_AUTH=true`, the execution role (not you) needs `rds-db:connect` on the DB user resource.

### 4.3 Diagnose permission problems fast

```bash
aws sts get-caller-identity                       # who am I?
aws iam list-attached-user-policies --user-name ME  # what can I do? (or check the role)
agentcore status --json | jq '.agents[0].runtimeArn'
```

`AccessDenied` at deploy → your identity lacks create rights. `AccessDenied` at invoke → missing `InvokeAgentRuntime` (or wrong ARN/region). Errors **inside** a successful invoke (Bedrock/secret/DB failures in `agentcore logs`) → the **execution role**, not you.

---

## 5. Bedrock model access

Separate from IAM: each foundation model must be **enabled** in each region (Bedrock console → Model access). Symptoms of skipping this: deploy succeeds, `agentcore invoke` returns model-access errors. Enable:

- `us.anthropic.claude-sonnet-4-20250514-v1:0` (or whatever `BEDROCK_MODEL_ID` is),
- `amazon.titan-embed-text-v2` (whatever `BEDROCK_EMBEDDING_MODEL_ID` is).

Embedding model + dims are a hard contract with your ingestion pipeline: query-time and stored vectors must come from the same model. Our `embeddings.py` raises on dimension mismatch — that error means the pipeline and the agent disagree, not that AWS is broken.

---

## 6. Project setup (this repo)

This repo is a **bring-your-own-code** layout (`main.py` + `src/`), not CLI-scaffolded, so onboarding is:

```bash
pip install -r requirements.txt
cp .env.example .env   # fill DB_HOST, DB_PASSWORD (or DB_SECRET_ARN), regions
psql "$DATABASE_URL" -f sql/schema.sql   # once per DB; pipeline owns the data
python scripts/verify_db.py   # live DB + Bedrock check
python scripts/test_local.py  # offline, no creds
python main.py                # local serve on :8080 like the Runtime does
```

Deploy wiring (our scripts do this; manual equivalent):

```bash
agentcore configure --entrypoint main.py --non-interactive
agentcore deploy
agentcore invoke --prompt "VPN error 809 on Windows 11, how was it fixed before?"
```

CLI-scaffolded projects instead have `agentcore/agentcore.json` (agents, memory, gateways) + `agentcore/aws-targets.json` (accounts/regions) managed by `agentcore create` / `agentcore add`. Either layout deploys with the same `agentcore deploy`.

---

## 7. Deploy: what happens and how

`agentcore deploy` (CodeZip, the default):

1. Zips your code (`main.py`, `src/`, deps from `requirements.txt`/`pyproject.toml`).
2. Synthesizes CDK → CloudFormation provisions/updates the Runtime, endpoint, roles, log groups.
3. Creates the Runtime endpoint serving `POST /invocations`, `GET /ping`.
4. Wires CloudWatch logging/observability.

Useful variants:

```bash
agentcore deploy --dry-run     # preview without changing anything
agentcore deploy --diff        # show infra diff
agentcore deploy -y --json     # non-interactive, machine-readable (CI/CD)
agentcore status               # runtime ARN, endpoint, state
agentcore status --json | jq .
agentcore dev                  # local dev server instead of deploying
agentcore package              # build the artifact without deploying
```

Container path (`Dockerfile` in repo): build → push to ECR → Runtime points at `containerUri`. Requirements the repo already satisfies: non-root user, listen on `8080`, small image. Our scripts automate both paths.

First deploy takes minutes (CDK bootstrap + image build); later ones are incremental.

---

## 8. Invoke, sessions, streaming

```bash
agentcore invoke --prompt "Outlook keeps disconnecting — known fixes?"
agentcore invoke --prompt "..." --stream            # token streaming
agentcore invoke --session-id abc123 "..."         # continue a conversation
agentcore invoke --new-session "..."               # force fresh session
agentcore invoke --prompt-file prompt.json --json  # long prompts / automation
```

Programmatic (backend proxy pattern — see README §External Access):

```python
import boto3, json
c = boto3.client("bedrock-agentcore", region_name="us-east-1")
r = c.invoke_agent_runtime(
    agentRuntimeArn="<RUNTIME_ARN>", qualifier="DEFAULT",
    runtimeSessionId="<uuid-per-conversation>",
    contentType="application/json", accept="application/json",
    payload=json.dumps({"prompt": "..."}).encode(),
)
body = r["response"].read()   # streaming body; relay chunks for live UX
```

Payload contract for **our** agent: `{"prompt": str, "top_k"?: int, "session_id"?: str, "agent"?: "full|triage|knowledge|rca|solution"}` → `{"result": str, "session_id": str, "agent": str, "triage": {...}, "incidents": [...], "rca": {...}, "solution": {...}}`. Default `agent: "full"` runs the whole pipeline triage → knowledge → rca → solution; pass a single mode to run one stage (useful for debugging — e.g. `"agent": "triage"` isolates classification). `top_k` is per-request; retrieval defaults live in Runtime env vars.

---

## 9. Networking: reaching PostgreSQL

The Runtime must have a network path to RDS/Aurora. Checklist:

- [ ] RDS security group allows inbound `5432` **from the Runtime's security group/VPC**, not `0.0.0.0`.
- [ ] Same region (or peered/transit-routed VPCs); DNS resolves the endpoint from the Runtime.
- [ ] `DB_SSLMODE=require`; `STATEMENT_TIMEOUT_MS` bounds runaway queries.
- [ ] Test from the agent side, not just your laptop: `scripts/verify_db.py` passes locally but the Runtime fails → it is networking/SGs, not credentials.
- [ ] Pool sizing (`DB_POOL_MIN/MAX`) modest — microVMs multiply connections.

If the DB is publicly inaccessible by design, the Runtime needs VPC attachment to reach it — configure at Runtime creation and verify with a minimal invoke before debugging the agent.

---

## 10. Auth for outside callers (IAM vs JWT)

A Runtime supports **one** inbound auth method at a time (separate versions/endpoints for different methods):

| Caller | Use | How |
|---|---|---|
| Service-to-service, backend proxy, your deploy scripts | **IAM SigV4** (default, nothing to configure) | Caller signs with creds holding `InvokeAgentRuntime` on the ARN |
| Browser UI with enterprise SSO (Entra ID / Cognito / Okta) | **JWT bearer** | Runtime configured with a JWT authorizer (IdP discovery URL + allowed client IDs); UI sends `Authorization: Bearer <token>` on raw HTTPS invocations |

Our repo's recommended shape: **SSO at your backend, IAM from backend to Runtime** — no Runtime reconfig, no AWS creds in the browser. Full setup (Entra app registration, MSAL.js, validating proxy) is in README §External Access via Entra ID SSO. Rules that prevent incidents:

- Derive user identity from the **validated** JWT (`sub`/`oid`), never client input.
- Never forward browser input into `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` (needs `InvokeAgentRuntimeForUser`; that's an impersonation primitive if fed from clients).
- A runtime can only do one method — don't try to mix IAM and JWT on the same endpoint.

---

## 11. Environment variables & secrets on the Runtime

Set on the Runtime (console or IaC), **not** baked into code/image:

| Group | Vars |
|---|---|
| Model | `BEDROCK_MODEL_ID`, `BEDROCK_REGION`, `BEDROCK_MAX_TOKENS`, `BEDROCK_TEMPERATURE` |
| Embeddings (must match pipeline!) | `BEDROCK_EMBEDDING_MODEL_ID`, `BEDROCK_EMBEDDING_REGION`, `EMBEDDING_DIMS` |
| DB | `DB_HOST/PORT/NAME/USER`, `DB_SSLMODE`, pool + timeout tuning |
| DB credentials (pick one) | `DB_PASSWORD` (dev only) · `DB_SECRET_ARN` (Secrets Manager JSON — see README §Secrets Manager Setup) · `DB_USE_IAM_AUTH=true` (RDS token) |
| Retrieval | `DEFAULT_TOP_K`, `MAX_TOP_K`, `MIN_SIMILARITY`, `HYBRID_KEYWORD_WEIGHT` |

Repo-specific caveats (as of now): the Secrets client uses `BEDROCK_REGION` (keep the secret co-located), and `DB_HOST` must be set even when the secret carries `host`. Redeploy or recycle sessions after changing secrets; new microVM pools read fresh values.

### Storing extra global variables in Secrets Manager

Rule of thumb for **where each value lives**:

| Value type | Put it in | Why |
|---|---|---|
| Non-sensitive config (`BEDROCK_MODEL_ID`, `DEFAULT_TOP_K`, …) | Runtime **environment variables** | Visible, versionable, trivial to change |
| Passwords, API keys, any credential | **Secrets Manager** | Encrypted, access-logged, rotatable, never in code/image/logs |
| DB credentials specifically | The existing `DB_SECRET_ARN` JSON (see README §Secrets Manager Setup) | Already wired into `db/connection.py` |

**To add a new global secret** (say an internal API key the agent needs):

1. Create one JSON secret holding all related keys together (fewer ARNs to manage):
   ```bash
   aws secretsmanager create-secret \
     --name snow-ka/global-config \
     --description "Shared sensitive config for the ServiceNow KA" \
     --secret-string '{"INTERNAL_API_KEY":"...","THIRD_PARTY_TOKEN":"..."}' \
     --region us-east-1
   ```
2. Grant the **execution role** (not your user) `secretsmanager:GetSecretValue` on that ARN (+ `kms:Decrypt` if CMK-encrypted) — same pattern as §4.2.
3. Reference it from the Runtime: either a new env var holding the ARN (e.g. `APP_SECRET_ARN`), keeping values out of env snapshots.

All service secrets share one loader (`knowledge_agent/secrets.py`): `load_service_secret()` fetches once per microVM into a thread-safe cache (never per request, values never logged), and `preload_service_credentials()` — called at the top of every `invoke` in `main.py` — warms every configured ARN so misconfiguration fails fast on cold start instead of mid-answer. `get_snow_credentials()` / `get_jira_credentials()` layer secret-over-env fallback; check `.available` before calling out to that service.

Rotation works the same as the DB secret: update the value, new sessions/microVMs read fresh; redeploy to force immediate pickup. Never log secret values — our code logs hosts and usernames only.

### AgentCore Memory service (durable recall)

This repo's conversation memory is in-process (`MemorySaver`): same `session_id` continues a thread, but a cold microVM or redeploy wipes it. Tune it with `MEMORY_ENABLED` / `MEMORY_MAX_TURNS` (restart/redeploy to apply). For memory that **survives restarts** and distills cross-session knowledge, use the managed Memory service:

```bash
agentcore add memory --name SupportMemory \
  --strategies SEMANTIC,SUMMARIZATION,USER_PREFERENCE --expiry 30
agentcore deploy
# or: aws bedrock-agentcore-control create-memory --name SupportMemory \
#       --event-expiry-duration 30   (+ strategies per API docs)
```

- **Strategies** pick what gets extracted: `SEMANTIC` (facts), `SUMMARIZATION` (session summaries), `USER_PREFERENCE` (user settings), `EPISODIC` (significant events + reflections). Change them by re-running the command (or the update API) + redeploy.
- **Expiry** (`--expiry 30`) bounds raw-event retention; long-term records follow strategy consolidation.
- Each memory surfaces as `MEMORY_<NAME>_ID` — set our `AGENTCORE_MEMORY_ID` to it. The execution role needs memory data-plane access scoped to the memory ARN.
- **Verify:** same-`session_id` invokes, then list the actor's long-term records. Extraction is async (~20–40s after the session), so don't expect instant records.
- **Code status:** the ID is read into settings, but recall injection into the knowledge step is not implemented yet (roadmap) — setting the ID alone changes nothing today.
- **Watch-outs:** records missing right after a session (async lag, not an error); wrong-actor namespaces when `session_id`/actor mapping changes; expiry set so short that raw events vanish before extraction consolidates them.

---

## 12. Observability: logs, traces, metrics

```bash
agentcore logs                        # stream/search runtime logs
agentcore logs --help                 # filter by time/runtime/session
agentcore traces list                 # recent traces for the deployed agent
agentcore traces get <id>             # download one trace as JSON
agentcore status                      # endpoint state, ARNs
```

In CloudWatch: the Runtime writes to its own log group (typically under `/aws/bedrock-agentcore/runtimes/<agent-id>-<endpoint>` — *(verify)* exact name in your console), plus `ActiveSessionCount` and standard metrics in the `AWS/Bedrock-AgentCore` namespace. Enable CloudWatch Transaction Search for end-to-end trace view (one-time console toggle).

Triage order for a bad invoke: **1.** `agentcore status` (is it deployed/healthy?) → **2.** re-invoke with `--json` (exact error) → **3.** `agentcore logs` around the timestamp (agent/tool stack trace?) → **4.** `traces get` (which step — embed, SQL, LLM?).

---

## 13. Update, version, and clean up

- **Update:** edit code → `agentcore deploy` again (same command; creates a new revision). Test with `agentcore invoke --new-session` to avoid stale-session confusion.
- **Preview first:** `agentcore deploy --dry-run` / `--diff`.
- **Remove a resource** (memory, gateway, endpoint): `agentcore remove <type> <name>` then `agentcore deploy` to apply.
- **Full teardown:** remove resources via the CLI and redeploy; for account-level cleanup use the generated CDK/CloudFormation stacks in the console *(verify exact stack names in `agentcore status --json` — don't delete `CDKToolkit` if other projects use it)*.
- **Pin versions:** note the CLI version (`agentcore --version`) and `requirements.txt` pins alongside each deploy so regressions are bisectable.

---

## 14. Troubleshooting bible

### A. Install / login / permissions

| # | Symptom | Cause → Fix |
|---|---|---|
| 1 | `agentcore: command not found` | CLI not installed → `npm install -g @aws/agentcore` (needs Node 20+). |
| 2 | `Unable to locate credentials` / `ExpiredToken` | No/invalid AWS creds → `aws configure` or `export AWS_PROFILE=...`; SSO users: re-login (`aws sso login --profile ...`), then `aws sts get-caller-identity`. |
| 3 | `AccessDenied` on deploy (`CreateAgentRuntime`, CloudFormation, IAM) | Your identity lacks rights → attach `BedrockAgentCoreFullAccess` (dev) or scoped custom policy; first deploy may need IAM + CDK bootstrap rights — involve your admin. |
| 4 | `AccessDenied` on `agentcore invoke` | Missing `bedrock-agentcore:InvokeAgentRuntime` on that ARN/region → fix policy; confirm ARN via `agentcore status --json` (easy to invoke the wrong region's stale ARN). |
| 5 | `agentcore deploy` hangs on first run | CDK bootstrap in progress → wait; on timeout, check CloudFormation console for the failed stack event (usually a permissions issue, see #3). |

### B. Configure / package

| # | Symptom | Cause → Fix |
|---|---|---|
| 6 | `agentcore configure` prompts forever / wrong project picked | Run from repo root with `--entrypoint main.py --non-interactive`; our deploy scripts do this. |
| 7 | Deploy bundle huge / slow | Vendored `.venv`, `__pycache__`, `.env` packaged → check `.dockerignore`/package excludes; never ship `.env`. |
| 8 | `ModuleNotFoundError` at runtime, works locally | `requirements.txt` / `pyproject.toml` missing a dep (e.g. `pgvector`, `psycopg`) → add it, redeploy. Local venv masks this — compare against a fresh `pip install -r requirements.txt`. |

### C. Invoke / agent behavior

| # | Symptom | Cause → Fix |
|---|---|---|
| 9 | `{"result": "Please provide a 'prompt' string..."}` | Caller sent empty/missing `prompt` → fix caller payload (our `main.py` guard). |
| 10 | Model access / `ValidationException` mentioning the model ID | Model not enabled in that region → Bedrock console → Model access → enable; or wrong `BEDROCK_MODEL_ID` for the region. |
| 11 | `Embedding dim mismatch` | Query embedding model ≠ ingestion model → align `BEDROCK_EMBEDDING_MODEL_ID`/`EMBEDDING_DIMS` with the pipeline and re-embed. AWS is fine; your config disagrees with the table. |
| 12 | Empty results / low similarity scores | Corpus not embedded (`embedding IS NULL`), wrong table, or `MIN_SIMILARITY` too high → `SELECT count(*), count(embedding) FROM snow_closed_incidents;` then tune `DEFAULT_TOP_K` / `HYBRID_KEYWORD_WEIGHT`. |
| 13 | Agent answers without `[INC]` citations | System prompt not loaded or tools failing silently → `agentcore logs` for tool errors; tool failures return `{error}` payloads by design — read them, they name the cause. |
| 14 | Timeouts on long questions | LLM + retrieval exceeding limits → raise client timeout (`AGENT_TIMEOUT_S=180` in local UI), lower `BEDROCK_MAX_TOKENS`, check `STATEMENT_TIMEOUT_MS` vs slow queries (`EXPLAIN` the hybrid SQL; confirm HNSW/GIN indexes exist). |
| 15 | Stale answers after redeploy | Old session resumed → invoke with `--new-session` / fresh `session_id`. |
| 16 | Wrong stage blamed (bad category? bad fix?) | Isolate with `"agent": "triage"` / `"knowledge"` / `"rca"` / `"solution"` to find the failing stage; triage has a heuristic fallback when the LLM is unreachable, so check `triage` in the response to see which path was taken. |

### D. Secrets / DB / network (execution-role side)

| # | Symptom | Cause → Fix |
|---|---|---|
| 17 | `DB_HOST is not set` | Runtime env vars missing (also required alongside `DB_SECRET_ARN` in current code). |
| 18 | `AccessDeniedException` on `GetSecretValue` | **Execution role** lacks `secretsmanager:GetSecretValue` (+ `kms:Decrypt` for CMK) → attach to the execution role, not your user. |
| 19 | Secret found, auth still fails | Secret JSON stale (rotated password, old host) or secret in a different region than `BEDROCK_REGION` → `aws secretsmanager get-secret-value --secret-id <ARN>` and compare fields. |
| 20 | Connection timeout / refused to RDS | Security groups / VPC path (works from laptop, fails from Runtime is the tell) → allow `5432` from the Runtime SG/VPC; check DNS + `DB_SSLMODE`. |
| 21 | `password authentication failed for ka_reader` | Wrong secret value or `DB_PASSWORD` shadowing → unset `DB_PASSWORD` when using `DB_SECRET_ARN`; verify role exists (`sql/schema.sql` creates it). |
| 22 | Too many DB connections | Pool `MAX` × microVMs exceeded RDS `max_connections` → lower `DB_POOL_MAX`, enable RDS Proxy, or raise instance limits. |

### E. UI / SSO (outside callers)

| # | Symptom | Cause → Fix |
|---|---|---|
| 23 | Browser CORS errors calling AgentCore directly | By design — don't call it from browsers → use the backend proxy (`/api/ask` pattern in README). |
| 24 | `AADSTS50011` redirect mismatch | Redirect URI not registered → add exact local + deployed origins as SPA URIs in the Entra app. |
| 25 | Backend rejects a fresh Entra token | `aud`/`iss` mismatch (wrong client/tenant) or clock skew → align IDs, check `exp`. |
| 26 | 401s after ~1 hour | Token expired, silent renewal blocked (e.g. Conditional Access) → handle `InteractionRequiredAuthError` → interactive re-login. |

### F. The universal drill (any unknown failure)

```bash
agentcore --version && aws sts get-caller-identity   # tooling + identity sane?
agentcore status --json                              # deployed? right ARN/region?
agentcore invoke --prompt "ping" --json              # exact error text
agentcore logs                                       # stack trace near that timestamp
python scripts/verify_db.py                          # is it the DB/Bedrock path?
git status && git diff --stat                        # what changed since it last worked?
```

---

## 15. Command cheat sheet

```bash
# setup
npm install -g @aws/agentcore
aws configure && aws sts get-caller-identity

# project (this repo layout)
agentcore configure --entrypoint main.py --non-interactive
agentcore dev                                  # local dev server
agentcore deploy --dry-run                     # preview
agentcore deploy -y --json                     # deploy (CI-friendly)
agentcore status --json

# operate
agentcore invoke --prompt "..." --stream
agentcore invoke --session-id <id> "..."
agentcore logs
agentcore traces list && agentcore traces get <id>

# our repo scripts
./scripts/publish-to-agentcore.sh [--mode container]   # deploy (bash)
.\scripts\Publish-ToAgentCore.ps1 [-Mode Container]    # deploy (PowerShell)
python scripts/verify_db.py                        # live DB + Bedrock check
python ui/server.py                                  # local test UI on :3000
```

---

## 16. Official references

- AgentCore Developer Guide (docs.aws.amazon.com/bedrock-agentcore) — concepts, permissions, auth, observability
- CLI reference: `agentcore --help` + `aws/agentcore-cli` on GitHub (`docs/commands.md`, `docs/PERMISSIONS.md`)
- Samples: `awslabs/amazon-bedrock-agentcore-samples` (esp. `01-features/05-authenticate-and-authorize`)
- Runtime SDK: `aws/bedrock-agentcore-sdk-python` (the `BedrockAgentCoreApp` contract our `main.py` implements)
- IAM actions index: `bedrock-agentcore:*` (Service Authorization Reference) for least-privilege policies

*Keep this guide next to the code: when you fix something new, add a row to §14 with the exact error text — future-you will grep for it.*
