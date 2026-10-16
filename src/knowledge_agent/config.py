"""Centralised, env-driven configuration for the Knowledge Agent.

All values can be set via environment variables (see .env.example).
On AgentCore Runtime, set them as environment variables on the Runtime
or inject via Secrets Manager / Parameter Store at deploy time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)).strip())
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)).strip())
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name, str(default)).strip().lower()
    return val in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class Settings:
    # Bedrock LLM
    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0"
    bedrock_region: str = "us-east-1"
    bedrock_max_tokens: int = 2048
    bedrock_temperature: float = 0.2

    # Bedrock embeddings — MUST match ingestion pipeline
    embedding_model_id: str = "amazon.titan-embed-text-v2"
    embedding_region: str = "us-east-1"
    embedding_dims: int = 1024

    # PostgreSQL
    db_host: str = ""
    db_port: int = 5432
    db_name: str = "knowledge"
    db_user: str = "ka_reader"
    db_password: str = ""
    db_sslmode: str = "require"
    db_pool_min: int = 1
    db_pool_max: int = 5
    db_secret_arn: str = ""
    db_use_iam_auth: bool = False
    statement_timeout_ms: int = 15000

    # Service secrets (Secrets Manager ARNs; see secrets.py).
    # Each is optional; per-service env vars remain as local-dev fallback.
    app_secret_arn: str = ""
    snow_secret_arn: str = ""
    jira_secret_arn: str = ""

    # Retrieval
    default_top_k: int = 5
    max_top_k: int = 20
    min_similarity: float = 0.0
    keyword_weight: float = 0.3

    # Agent
    max_tool_steps: int = 6
    default_mode: str = "full"

    # Conversation memory (LangGraph checkpointer, in-process).
    # Same session_id continues the thread; a cold microVM/restart wipes it.
    # For durable cross-session memory, see the AgentCore Memory service
    # (AGENTCORE_MEMORY_ID + README "Memory settings").
    memory_enabled: bool = True
    memory_max_turns: int = 10  # keep last N user turns per thread; 0 = unlimited
    agentcore_memory_id: str = ""


def load_settings() -> Settings:
    return Settings(
        bedrock_model_id=_get("BEDROCK_MODEL_ID", Settings.bedrock_model_id),
        bedrock_region=_get("BEDROCK_REGION", Settings.bedrock_region),
        bedrock_max_tokens=_get_int("BEDROCK_MAX_TOKENS", Settings.bedrock_max_tokens),
        bedrock_temperature=_get_float("BEDROCK_TEMPERATURE", Settings.bedrock_temperature),
        embedding_model_id=_get("BEDROCK_EMBEDDING_MODEL_ID", Settings.embedding_model_id),
        embedding_region=_get("BEDROCK_EMBEDDING_REGION", Settings.embedding_region),
        embedding_dims=_get_int("EMBEDDING_DIMS", Settings.embedding_dims),
        db_host=_get("DB_HOST", ""),
        db_port=_get_int("DB_PORT", 5432),
        db_name=_get("DB_NAME", "knowledge"),
        db_user=_get("DB_USER", "ka_reader"),
        db_password=_get("DB_PASSWORD", ""),
        db_sslmode=_get("DB_SSLMODE", "require"),
        db_pool_min=_get_int("DB_POOL_MIN", 1),
        db_pool_max=_get_int("DB_POOL_MAX", 5),
        db_secret_arn=_get("DB_SECRET_ARN", ""),
        db_use_iam_auth=_get_bool("DB_USE_IAM_AUTH", False),
        statement_timeout_ms=_get_int("STATEMENT_TIMEOUT_MS", 15000),
        app_secret_arn=_get("APP_SECRET_ARN", ""),
        snow_secret_arn=_get("SNOW_SECRET_ARN", ""),
        jira_secret_arn=_get("JIRA_SECRET_ARN", ""),
        default_top_k=_get_int("DEFAULT_TOP_K", 5),
        max_top_k=_get_int("MAX_TOP_K", 20),
        min_similarity=_get_float("MIN_SIMILARITY", 0.0),
        keyword_weight=_get_float("HYBRID_KEYWORD_WEIGHT", 0.3),
        max_tool_steps=_get_int("KA_MAX_TOOL_STEPS", 6),
        default_mode=_get("AGENT_MODE", "full").lower() or "full",
        memory_enabled=_get_bool("MEMORY_ENABLED", True),
        memory_max_turns=_get_int("MEMORY_MAX_TURNS", 10),
        agentcore_memory_id=_get("AGENTCORE_MEMORY_ID", ""),
    )
