"""RED-TEAM PoC: handler spend/abuse + crash probes.

Goal: find any path that (a) reaches answer() (=> embedding+LLM cost) bypassing
a validation gate, or (b) crashes the handler with an unhandled exception
(leaking a stack trace / 500 without the safe envelope).

answer() is mocked as a tripwire: if it is ever called, real money would be
spent in prod. We assert on whether the tripwire fired.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "api"))
sys.path.insert(0, str(REPO_ROOT / "src"))

import handler  # type: ignore[import-not-found]  # noqa: E402

_KNOWN = "105_amazon_com"
_STUB = {
    "answerable": True, "text": "x [1].", "citations": [], "policy_id": _KNOWN, "model": "stub",
}


def _ev(body: Any, *, raw: bool = False, **extra: Any) -> dict[str, Any]:
    e: dict[str, Any] = {"body": body if raw else json.dumps(body)}
    e.update(extra)
    return e


def _run_with_tripwire(event: dict[str, Any]):
    """Run handler with answer() as a tripwire. Returns (resp, answer_called)."""
    tripwire = MagicMock(return_value=_STUB)
    with patch("handler._get_retriever", return_value=MagicMock()), \
         patch("handler._ensure_answer_loaded"), \
         patch.object(handler, "answer", tripwire):
        resp = handler.handler(event, None)
    return resp, tripwire.called


# ---------------------------------------------------------------------------
# A. isBase64Encoded body-size bypass
# ---------------------------------------------------------------------------

def test_base64_body_not_decoded():
    real = json.dumps({"query": "x", "policy_id": _KNOWN})
    b64 = base64.b64encode(real.encode()).decode()
    resp, called = _run_with_tripwire(_ev(b64, raw=True, isBase64Encoded=True))
    assert not called, "base64 body reached answer() without decode"
    assert resp["statusCode"] in (400, 200)


# ---------------------------------------------------------------------------
# B. top_k type confusion
# ---------------------------------------------------------------------------

def test_top_k_bool_true_accepted():
    resp, called = _run_with_tripwire(
        _ev({"query": "hi", "policy_id": _KNOWN, "top_k": True})
    )
    assert resp["statusCode"] == 200
    assert called


def test_top_k_float_string_rejected():
    resp, called = _run_with_tripwire(
        _ev({"query": "hi", "policy_id": _KNOWN, "top_k": "3.5"})
    )
    assert resp["statusCode"] == 400
    assert not called


def test_top_k_huge_float_rejected():
    resp, called = _run_with_tripwire(
        _ev({"query": "hi", "policy_id": _KNOWN, "top_k": 1e9})
    )
    assert resp["statusCode"] == 400
    assert not called


# ---------------------------------------------------------------------------
# C. unicode length vs byte length on query
# ---------------------------------------------------------------------------

def test_query_unicode_len_under_500_but_huge_bytes():
    q = "\U0001F4A9" * 500  # 500 code points, 2000 bytes
    resp, called = _run_with_tripwire(_ev({"query": q, "policy_id": _KNOWN}))
    assert len(q) == 500
    assert resp["statusCode"] == 200
    assert called  # billable: by design (<=500 chars)


# ---------------------------------------------------------------------------
# D. malformed event shapes (crash probes) — must return clean dict, no spend
# ---------------------------------------------------------------------------

def test_event_no_body_key():
    resp, called = _run_with_tripwire({})
    assert resp["statusCode"] == 400
    assert not called


# FIXED (was HIGH finding #2): a non-string event["body"] (dict/list/int from a
# direct/console invoke) used to raise an unhandled TypeError → 502 + full
# traceback in CloudWatch. The handler now rejects a non-string body with a
# clean 400 BEFORE any json.loads/len, never reaching answer(). Regression guard.

def test_body_is_dict_returns_clean_400():
    resp, called = _run_with_tripwire({"body": {"query": "x", "policy_id": _KNOWN}})
    assert resp["statusCode"] == 400
    assert not called
    assert json.loads(resp["body"]).get("error")  # safe envelope, no traceback


def test_body_is_int_returns_clean_400():
    resp, called = _run_with_tripwire({"body": 12345})
    assert resp["statusCode"] == 400
    assert not called


def test_body_is_list_returns_clean_400():
    resp, called = _run_with_tripwire({"body": ["query", "policy_id"]})
    assert resp["statusCode"] == 400
    assert not called
