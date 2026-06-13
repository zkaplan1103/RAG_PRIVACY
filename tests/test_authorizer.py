"""Unit tests for api/authorizer.py — the x-api-key Lambda authorizer.

The authorizer is the primary anti-abuse control: it must FAIL CLOSED on every
error path (no secret ARN, secret load failure, missing header, wrong key) and
only return isAuthorized=true for an exact, constant-time key match.

boto3 is not installed locally, so we inject a fake boto3 into sys.modules and
reset the authorizer's module-level cache between tests.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "api"))

_EXPECTED_KEY = "s3cr3t-test-key-0123456789abcdef"
_SECRET_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:policylens/api_key-AbCdEf"


def _install_fake_boto3(secret_string: str | None, *, raise_on_get: bool = False) -> None:
    """Inject a fake boto3 whose secretsmanager client returns secret_string."""

    class _FakeClient:
        def get_secret_value(self, SecretId: str) -> dict[str, Any]:  # noqa: N803
            if raise_on_get:
                raise RuntimeError("boom")
            return {"SecretString": secret_string}

    fake = types.ModuleType("boto3")
    fake.client = lambda service: _FakeClient()  # type: ignore[attr-defined]
    sys.modules["boto3"] = fake


def _load_authorizer():
    """Import authorizer fresh so the cached key resets between tests."""
    for mod_name in list(sys.modules.keys()):
        if mod_name == "authorizer":
            del sys.modules[mod_name]
    import authorizer  # type: ignore[import-not-found]
    return authorizer


def _event(api_key: str | None) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if api_key is not None:
        headers["x-api-key"] = api_key
    return {"headers": headers}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("API_KEY_SECRET_ARN", raising=False)
    # Remove any fake boto3 left by a prior test
    sys.modules.pop("boto3", None)
    yield
    sys.modules.pop("boto3", None)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_correct_key_authorized(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_KEY_SECRET_ARN", _SECRET_ARN)
    _install_fake_boto3(_EXPECTED_KEY)
    authorizer = _load_authorizer()
    result = authorizer.handler(_event(_EXPECTED_KEY), None)
    assert result == {"isAuthorized": True}


# ---------------------------------------------------------------------------
# Fail-closed paths
# ---------------------------------------------------------------------------

def test_wrong_key_denied(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_KEY_SECRET_ARN", _SECRET_ARN)
    _install_fake_boto3(_EXPECTED_KEY)
    authorizer = _load_authorizer()
    assert authorizer.handler(_event("wrong-key"), None) == {"isAuthorized": False}


def test_missing_header_denied(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_KEY_SECRET_ARN", _SECRET_ARN)
    _install_fake_boto3(_EXPECTED_KEY)
    authorizer = _load_authorizer()
    assert authorizer.handler(_event(None), None) == {"isAuthorized": False}


def test_empty_header_denied(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_KEY_SECRET_ARN", _SECRET_ARN)
    _install_fake_boto3(_EXPECTED_KEY)
    authorizer = _load_authorizer()
    assert authorizer.handler(_event(""), None) == {"isAuthorized": False}


def test_no_secret_arn_denied():
    # API_KEY_SECRET_ARN unset → cannot load expected key → deny.
    _install_fake_boto3(_EXPECTED_KEY)
    authorizer = _load_authorizer()
    assert authorizer.handler(_event(_EXPECTED_KEY), None) == {"isAuthorized": False}


def test_secret_load_failure_denied(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_KEY_SECRET_ARN", _SECRET_ARN)
    _install_fake_boto3(None, raise_on_get=True)
    authorizer = _load_authorizer()
    assert authorizer.handler(_event(_EXPECTED_KEY), None) == {"isAuthorized": False}


def test_prefix_of_key_denied(monkeypatch: pytest.MonkeyPatch):
    # Constant-time compare must reject a prefix, not just unequal-length.
    monkeypatch.setenv("API_KEY_SECRET_ARN", _SECRET_ARN)
    _install_fake_boto3(_EXPECTED_KEY)
    authorizer = _load_authorizer()
    assert authorizer.handler(_event(_EXPECTED_KEY[:-1]), None) == {"isAuthorized": False}


def test_missing_headers_key_entirely(monkeypatch: pytest.MonkeyPatch):
    # Event with no "headers" key at all must not crash → deny.
    monkeypatch.setenv("API_KEY_SECRET_ARN", _SECRET_ARN)
    _install_fake_boto3(_EXPECTED_KEY)
    authorizer = _load_authorizer()
    assert authorizer.handler({}, None) == {"isAuthorized": False}
