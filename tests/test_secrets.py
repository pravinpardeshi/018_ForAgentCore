"""Unit tests for the secrets loader (no AWS needed — boto3 is mocked)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from knowledge_agent import secrets as S


class FakeSM:
    """Minimal Secrets Manager stub. Counts calls to prove caching."""

    def __init__(self, store):
        self.store = store
        self.calls = 0

    def get_secret_value(self, SecretId):
        self.calls += 1
        if SecretId not in self.store:
            raise RuntimeError(f"secret not found: {SecretId}")
        return {"SecretString": self.store[SecretId]}


@pytest.fixture
def sm(monkeypatch):
    store = {
        "arn:db": json.dumps({"username": "ka_reader", "password": "pw",
                              "host": "h", "port": 5432, "dbname": "k"}),
        "arn:snow": json.dumps({"instance_url": "https://x.service-now.com/",
                                "username": "svc", "password": "s3cret"}),
        "arn:jira": json.dumps({"base_url": "https://x.atlassian.net",
                                "email": "bot@x.com", "api_token": "tok"}),
        "arn:bad": "not-json{{{",
    }
    fake = FakeSM(store)
    monkeypatch.setattr("knowledge_agent.secrets.boto3.client",
                        lambda *a, **k: fake)
    S.clear_secret_cache()
    yield fake
    S.clear_secret_cache()


def _settings(**over):
    from knowledge_agent.config import Settings
    return Settings(**over)


def test_load_caches_per_process(sm):
    a = S.load_service_secret("arn:db", "us-east-1")
    b = S.load_service_secret("arn:db", "us-east-1")
    assert a is b and sm.calls == 1
    assert a["username"] == "ka_reader"


def test_load_rejects_non_json(sm):
    with pytest.raises(RuntimeError, match="not a JSON"):
        S.load_service_secret("arn:bad", "us-east-1")


def test_load_rejects_empty_arn(sm):
    with pytest.raises(ValueError):
        S.load_service_secret("", "us-east-1")


def test_snow_basic_from_secret(sm):
    creds = S.get_snow_credentials(_settings(snow_secret_arn="arn:snow"))
    assert creds.available and creds.auth_kind == "basic"
    assert creds.instance_url == "https://x.service-now.com"  # trailing slash stripped
    assert creds.password == "s3cret"


def test_snow_env_fallback(monkeypatch, sm):
    monkeypatch.setenv("SNOW_INSTANCE_URL", "https://y.service-now.com")
    monkeypatch.setenv("SNOW_CLIENT_ID", "cid")
    monkeypatch.setenv("SNOW_CLIENT_SECRET", "csec")
    creds = S.get_snow_credentials(_settings())
    assert creds.available and creds.auth_kind == "oauth"


def test_snow_unavailable_when_nothing_set(monkeypatch, sm):
    for v in ("SNOW_INSTANCE_URL", "SNOW_USERNAME", "SNOW_PASSWORD",
              "SNOW_CLIENT_ID", "SNOW_CLIENT_SECRET"):
        monkeypatch.delenv(v, raising=False)
    assert not S.get_snow_credentials(_settings()).available


def test_jira_from_secret(sm):
    creds = S.get_jira_credentials(_settings(jira_secret_arn="arn:jira"))
    assert creds.available
    assert (creds.base_url, creds.email) == ("https://x.atlassian.net", "bot@x.com")


def test_jira_env_fallback(monkeypatch, sm):
    monkeypatch.setenv("JIRA_BASE_URL", "https://y.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "a@b.c")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    assert S.get_jira_credentials(_settings()).available


def test_inject_app_secrets_prefers_existing_env(monkeypatch, sm):
    import knowledge_agent.secrets as S2

    fake = FakeSM({"arn:app": json.dumps({"SHARED_KEY": "from-secret",
                                          "KEPT_KEY": "from-secret"})})
    monkeypatch.setattr(S2.boto3, "client", lambda *a, **k: fake)
    monkeypatch.setenv("KEPT_KEY", "from-env")
    monkeypatch.delenv("SHARED_KEY", raising=False)
    injected = S2.inject_app_secrets(_settings(app_secret_arn="arn:app"))
    assert injected == {"SHARED_KEY": "from-secret"}
    assert os.environ["KEPT_KEY"] == "from-env"


def test_db_credentials_still_resolve_via_shared_loader(sm):
    from knowledge_agent.db.connection import _resolve_credentials

    host, port, dbname, user, password = _resolve_credentials(
        _settings(db_host="fallback", db_secret_arn="arn:db"))
    assert (host, port, dbname, user, password) == ("h", 5432, "k", "ka_reader", "pw")
    assert sm.calls == 1  # secret fetched once through the shared cache
