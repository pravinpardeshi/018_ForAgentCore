"""Central Secrets Manager loader for all service credentials.

On AgentCore there is no `.env` file: non-sensitive config travels as Runtime
environment variables, while every secret lives in AWS Secrets Manager and is
fetched ONCE per microVM (process-global thread-safe cache) — never per
request, and values are never logged.

Supported (all optional, all independently configurable):
  DB    via DB_SECRET_ARN   (consumed by db/connection.py)
  App   via APP_SECRET_ARN  (generic globals, injected into os.environ)
  SNOW  via SNOW_SECRET_ARN (ServiceNow-Codex basic or OAuth client-creds)
  Jira  via JIRA_SECRET_ARN (base_url + email + api_token)

Each service also accepts plain env vars as fallback for local dev, e.g.
SNOW_INSTANCE_URL / SNOW_USERNAME / SNOW_PASSWORD.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Optional

import boto3

log = logging.getLogger(__name__)

_cache: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _short_arn(arn: str) -> str:
    # Log something identifiable but not sensitive: last 2 ARN segments.
    parts = arn.split(":")[-1].split("/")[-2:]
    return "/".join(parts) if parts else "***"


def load_service_secret(secret_arn: str, region: str) -> dict[str, Any]:
    """Fetch a JSON secret, cached per process. Raises on empty/non-JSON."""
    secret_arn = (secret_arn or "").strip()
    if not secret_arn:
        raise ValueError("load_service_secret: secret_arn must be non-empty")

    with _lock:
        if secret_arn in _cache:
            return _cache[secret_arn]

    sm = boto3.client("secretsmanager", region_name=region)
    resp = sm.get_secret_value(SecretId=secret_arn)
    raw = resp.get("SecretString") or ""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise RuntimeError(
            f"Secret {_short_arn(secret_arn)} is not a JSON document. "
            "Store service credentials as a JSON object, e.g. "
            '\'{"username": "...", "password": "..."}\'.'
        )
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Secret {_short_arn(secret_arn)} must be a JSON object, "
            f"got {type(payload).__name__}."
        )

    with _lock:
        _cache[secret_arn] = payload
    log.info("Loaded secret %s (%d keys)", _short_arn(secret_arn), len(payload))
    return payload


def clear_secret_cache() -> None:
    """Drop all cached secrets. Used by tests; also handy after rotation."""
    with _lock:
        _cache.clear()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# --------------------------------------------------------------------------
# ServiceNow
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SnowCredentials:
    instance_url: str = ""
    username: str = ""
    password: str = ""
    client_id: str = ""
    client_secret: str = ""

    @property
    def auth_kind(self) -> str:
        if self.client_id and self.client_secret:
            return "oauth"
        if self.username and self.password:
            return "basic"
        return "none"

    @property
    def available(self) -> bool:
        return bool(self.instance_url) and self.auth_kind != "none"


def get_snow_credentials(settings=None) -> SnowCredentials:
    """ServiceNow creds: SNOW_SECRET_ARN wins, else SNOW_* env vars."""
    from knowledge_agent.config import load_settings

    settings = settings or load_settings()
    merged: dict[str, Any] = {}
    if settings.snow_secret_arn:
        merged.update(
            load_service_secret(settings.snow_secret_arn, settings.bedrock_region)
        )
    # Env vars fill gaps the secret left (handy for local dev).
    for key, env_name in (
        ("instance_url", "SNOW_INSTANCE_URL"),
        ("username", "SNOW_USERNAME"),
        ("password", "SNOW_PASSWORD"),
        ("client_id", "SNOW_CLIENT_ID"),
        ("client_secret", "SNOW_CLIENT_SECRET"),
    ):
        merged.setdefault(key, _env(env_name))
    return SnowCredentials(
        instance_url=str(merged.get("instance_url", "")).strip().rstrip("/"),
        username=str(merged.get("username", "")).strip(),
        password=str(merged.get("password", "")),
        client_id=str(merged.get("client_id", "")).strip(),
        client_secret=str(merged.get("client_secret", "")),
    )


# --------------------------------------------------------------------------
# Jira
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class JiraCredentials:
    base_url: str = ""
    email: str = ""
    api_token: str = ""

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.email and self.api_token)


def get_jira_credentials(settings=None) -> JiraCredentials:
    """Jira creds: JIRA_SECRET_ARN wins, else JIRA_* env vars."""
    from knowledge_agent.config import load_settings

    settings = settings or load_settings()
    merged: dict[str, Any] = {}
    if settings.jira_secret_arn:
        merged.update(
            load_service_secret(settings.jira_secret_arn, settings.bedrock_region)
        )
    for key, env_name in (
        ("base_url", "JIRA_BASE_URL"),
        ("email", "JIRA_EMAIL"),
        ("api_token", "JIRA_API_TOKEN"),
    ):
        merged.setdefault(key, _env(env_name))
    token = merged.get("api_token", "")
    return JiraCredentials(
        base_url=str(merged.get("base_url", "")).strip().rstrip("/"),
        email=str(merged.get("email", "")).strip(),
        api_token=str(token) if token else "",
    )


# --------------------------------------------------------------------------
# Generic app globals + cold-start preload
# --------------------------------------------------------------------------

def inject_app_secrets(settings=None) -> dict[str, str]:
    """Load APP_SECRET_ARN JSON and setdefault each string value into environ.

    Returns the injected {KEY: value} mapping (values kept in memory only).
    Existing env vars always win — the secret only fills gaps.
    """
    from knowledge_agent.config import load_settings

    settings = settings or load_settings()
    if not settings.app_secret_arn:
        return {}
    payload = load_service_secret(settings.app_secret_arn, settings.bedrock_region)
    injected: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(value, str) and key not in os.environ:
            os.environ[key] = value
            injected[key] = value
    if injected:
        log.info("Injected %d app secret(s): %s",
                 len(injected), sorted(injected))
    return injected


def preload_service_credentials(settings=None) -> None:
    """Warm the cache for every configured secret. Call once per cold start.

    Fails fast on misconfiguration (bad ARN, non-JSON, no IAM) instead of
    failing mid-answer on first use. No-ops for unconfigured services.
    """
    from knowledge_agent.config import load_settings

    settings = settings or load_settings()
    inject_app_secrets(settings)
    if settings.db_secret_arn:
        load_service_secret(settings.db_secret_arn, settings.bedrock_region)
    if settings.snow_secret_arn:
        get_snow_credentials(settings)
    if settings.jira_secret_arn:
        get_jira_credentials(settings)


__all__ = [
    "JiraCredentials",
    "SnowCredentials",
    "clear_secret_cache",
    "get_jira_credentials",
    "get_snow_credentials",
    "inject_app_secrets",
    "load_service_secret",
    "preload_service_credentials",
]
