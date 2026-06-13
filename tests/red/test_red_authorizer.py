"""RED-TEAM PoC: Lambda authorizer (api/authorizer.py) bypass probes.

The authorizer is the #1 wallet control. Try to make handler() reachable
(isAuthorized=True) without the real key, or to break the fail-closed guarantee.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "api"))

_EXPECTED = "real-secret-key"
_ARN = "arn:aws:secretsmanager:us-east-1:1:secret:policylens/api_key-x"


def _install_fake_boto3(secret: str | None, *, raise_on_get: bool = False) -> None:
    class _C:
        def get_secret_value(self, SecretId: str) -> dict[str, Any]:  # noqa: N803
            if raise_on_get:
                raise RuntimeError("boom")
            return {"SecretString": secret}
    fake = types.ModuleType("boto3")
    fake.client = lambda service: _C()  # type: ignore[attr-defined]
    sys.modules["boto3"] = fake


def _load():
    sys.modules.pop("authorizer", None)
    import authorizer  # type: ignore[import-not-found]
    return authorizer


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("API_KEY_SECRET_ARN", raising=False)
    sys.modules.pop("boto3", None)
    sys.modules.pop("authorizer", None)
    yield
    sys.modules.pop("boto3", None)
    sys.modules.pop("authorizer", None)


# ---------------------------------------------------------------------------
# Header-case attack: HTTP API v2 lowercases headers, but what if a client/
# proxy sends 'X-Api-Key'? The authorizer reads ONLY headers['x-api-key'].
# ---------------------------------------------------------------------------

def test_uppercase_header_key_denied(monkeypatch: pytest.MonkeyPatch):
    """If the event arrived with a non-lowercase header key, the lookup misses
    and we DENY (fail closed). Good — verify it does not crash or auth."""
    monkeypatch.setenv("API_KEY_SECRET_ARN", _ARN)
    _install_fake_boto3(_EXPECTED)
    a = _load()
    ev = {"headers": {"X-Api-Key": _EXPECTED}}  # wrong case
    assert a.handler(ev, None) == {"isAuthorized": False}


# ---------------------------------------------------------------------------
# Empty-secret fail-open probe: if Secrets Manager returns an EMPTY string as
# the configured key (operator created the secret but never set a value, which
# Terraform comments say is the default "empty so 403-by-default" state), is an
# empty x-api-key accepted? `not expected` should catch "" -> deny.
# ---------------------------------------------------------------------------

def test_empty_secret_value_denies_even_empty_presented(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_KEY_SECRET_ARN", _ARN)
    _install_fake_boto3("")  # secret value is empty string
    a = _load()
    # caller sends empty x-api-key too
    assert a.handler({"headers": {"x-api-key": ""}}, None) == {"isAuthorized": False}
    # caller sends nothing
    assert a.handler({"headers": {}}, None) == {"isAuthorized": False}


# ---------------------------------------------------------------------------
# headers is null (HTTP API can send headers: null when none present)
# ---------------------------------------------------------------------------

def test_headers_null_denied(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_KEY_SECRET_ARN", _ARN)
    _install_fake_boto3(_EXPECTED)
    a = _load()
    assert a.handler({"headers": None}, None) == {"isAuthorized": False}


# ---------------------------------------------------------------------------
# Secret-load failure caching: _CACHED_KEY stays None on failure, so a transient
# Secrets Manager failure does NOT poison the cache into a permanent allow.
# Verify a failed load denies, and a subsequent good load (new container) works.
# ---------------------------------------------------------------------------

def test_transient_secret_failure_then_success(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_KEY_SECRET_ARN", _ARN)
    _install_fake_boto3(None, raise_on_get=True)
    a = _load()
    assert a.handler({"headers": {"x-api-key": _EXPECTED}}, None) == {"isAuthorized": False}
    # Same container, secrets manager recovers -> reinstall working boto3.
    _install_fake_boto3(_EXPECTED)
    # _CACHED_KEY is still None (failure didn't cache), so it retries and works.
    assert a.handler({"headers": {"x-api-key": _EXPECTED}}, None) == {"isAuthorized": True}


# ---------------------------------------------------------------------------
# list-valued header (multi-value) — HTTP API v2 joins multi-values with comma,
# but a malformed event could pass a list. compare_digest on (str, list) raises
# TypeError -> NOT caught here -> authorizer crashes. A crashing authorizer in
# API GW returns 500 to the caller => request denied (fail closed at the gateway
# level) but it's an unhandled exception. Document behavior.
# ---------------------------------------------------------------------------

def test_list_valued_header(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_KEY_SECRET_ARN", _ARN)
    _install_fake_boto3(_EXPECTED)
    a = _load()
    ev = {"headers": {"x-api-key": [_EXPECTED]}}  # list, not str
    # FINDING (Low/Suspicion): hmac.compare_digest(list, str) raises TypeError,
    # unhandled in handler(). NOT reachable from a real HTTP API v2 event (the
    # gateway comma-joins multi-values into a str), and a crash still fails
    # closed at the gateway (500 -> request denied). Pin the current behavior.
    with pytest.raises(TypeError):
        a.handler(ev, None)
