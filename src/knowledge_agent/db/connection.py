"""Thread-safe lazy psycopg v3 connection pool for AgentCore Runtime.

Supports:
  1. Direct password via DB_PASSWORD
  2. Secrets Manager JSON secret via DB_SECRET_ARN
  3. RDS IAM auth token via DB_USE_IAM_AUTH=true

The pool is a process-global singleton so warm microVMs reuse connections.
"""

from __future__ import annotations

import json
import logging
import threading

import boto3

log = logging.getLogger(__name__)

_pool = None
_lock = threading.Lock()


def _resolve_credentials(settings):
    host, port, dbname, user, password = (
        settings.db_host,
        settings.db_port,
        settings.db_name,
        settings.db_user,
        settings.db_password,
    )

    if settings.db_secret_arn:
        sm = boto3.client("secretsmanager", region_name=settings.bedrock_region)
        secret = sm.get_secret_value(SecretId=settings.db_secret_arn)
        payload = json.loads(secret["SecretString"])
        host = payload.get("host", host)
        port = int(payload.get("port", port))
        dbname = payload.get("dbname", dbname)
        user = payload.get("username", user)
        password = payload.get("password", password or "")

    if settings.db_use_iam_auth:
        rds = boto3.client("rds", region_name=settings.bedrock_region)
        password = rds.generate_db_auth_token(
            DBHostname=host, Port=port, DBUsername=user
        )
        log.info("Using RDS IAM auth token for user %s", user)

    return host, port, dbname, user, password


def get_pool(settings=None):
    """Return the global ConnectionPool, creating it on first call."""
    global _pool
    if _pool is not None:
        return _pool

    from psycopg_pool import ConnectionPool

    from knowledge_agent.config import load_settings

    settings = settings or load_settings()

    if not settings.db_host:
        raise RuntimeError(
            "DB_HOST is not set. Configure PostgreSQL connection env vars "
            "(see .env.example) on the AgentCore Runtime."
        )

    host, port, dbname, user, password = _resolve_credentials(settings)

    conninfo = (
        f"host={host} port={port} dbname={dbname} "
        f"user={user} password={password} "
        f"sslmode={settings.db_sslmode} "
        f"options='-c statement_timeout={settings.statement_timeout_ms}'"
    )

    with _lock:
        if _pool is None:
            _pool = ConnectionPool(
                conninfo,
                min_size=settings.db_pool_min,
                max_size=settings.db_pool_max,
                kwargs={"autocommit": True},
                open=True,
            )
            log.info("PostgreSQL pool opened: %s:%s/%s", host, port, dbname)
    return _pool


def close_pool():
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None
