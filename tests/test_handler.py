"""Unit tests for api/handler.py — no API key, no index, no AWS needed.

Tests:
  - 400 on bad JSON
  - 400 on oversized body
  - 400 on empty/non-string query
  - 400 on query > 500 chars
  - 400 on missing fields
  - 400 on invalid top_k
  - 404 on unknown policy_id
  - 200 happy path (mocked answer())
  - Validation rejects BEFORE answer() is called (400 and 404 cases)
  - ValueError from answer() → 400

The handler is imported from api/handler.py; policylens.generate.answer is
monkeypatched to avoid any real embedding or LLM calls.
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup: make api/ and src/ importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "api"))
sys.path.insert(0, str(REPO_ROOT / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(body: Any, *, raw: bool = False) -> dict[str, Any]:
    """Build a minimal API Gateway proxy event."""
    if raw:
        raw_body = body
    else:
        raw_body = json.dumps(body)
    return {"body": raw_body, "httpMethod": "POST", "path": "/ask"}


def _load_handler():
    """Import handler fresh (to reset module-level state between tests)."""
    # Remove cached module so _SECRETS_LOADED etc. reset
    for mod_name in list(sys.modules.keys()):
        if "handler" in mod_name and "test_handler" not in mod_name:
            del sys.modules[mod_name]
    import handler  # type: ignore[import-not-found]
    return handler


# ---------------------------------------------------------------------------
# Stub answer (no API key, no retriever)
# ---------------------------------------------------------------------------

_STUB_ANSWER = {
    "answerable": True,
    "text": "The policy collects email addresses [1].",
    "citations": [
        {
            "chunk_id": "105_amazon_com::data_collection::c000",
            "section": "Data Collection",
            "quote": "we collect email addresses",
        }
    ],
    "policy_id": "105_amazon_com",
    "model": "stub",
}

_KNOWN_POLICY = "105_amazon_com"  # present in the built-in OPP-115 allowlist
_UNKNOWN_POLICY = "totally_unknown_policy_xyz_that_does_not_exist"


# ---------------------------------------------------------------------------
# Tests: 400 Bad Request
# ---------------------------------------------------------------------------


class TestBadRequest:
    def test_missing_body(self):
        import handler  # type: ignore[import-not-found]
        event = {"body": None}
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert "request_id" in body
        assert "required" in body["error"].lower() or "body" in body["error"].lower()

    def test_empty_body_string(self):
        import handler  # type: ignore[import-not-found]
        event = {"body": ""}
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400

    def test_invalid_json(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event("not-valid-json{{{", raw=True)
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert "json" in body["error"].lower()

    def test_json_not_object(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event([1, 2, 3])
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400

    def test_missing_query(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event({"policy_id": _KNOWN_POLICY})
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert "query" in body["error"].lower()

    def test_missing_policy_id(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event({"query": "What data is collected?"})
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert "policy_id" in body["error"].lower()

    def test_query_not_string(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event({"query": 42, "policy_id": _KNOWN_POLICY})
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert "query" in body["error"].lower()

    def test_query_empty(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event({"query": "", "policy_id": _KNOWN_POLICY})
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400

    def test_query_too_long(self):
        import handler  # type: ignore[import-not-found]
        long_query = "x" * 501
        event = _make_event({"query": long_query, "policy_id": _KNOWN_POLICY})
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert "500" in body["error"] or "max" in body["error"].lower()

    def test_query_exactly_500_chars_ok(self):
        """Query of exactly MAX_QUERY_CHARS should pass validation (hits policy check)."""
        import handler  # type: ignore[import-not-found]
        query_500 = "q" * 500
        event = _make_event({"query": query_500, "policy_id": _KNOWN_POLICY})
        with patch("handler._get_retriever"), \
             patch("handler._ensure_answer_loaded"), \
             patch.object(handler, "answer", _STUB_ANSWER):
            resp = handler.handler(event, None)
        # Should NOT be a 400 validation error (may be 200 or 500 from mock)
        assert resp["statusCode"] != 400

    def test_policy_id_not_string(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event({"query": "test?", "policy_id": 123})
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400

    def test_policy_id_empty(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event({"query": "test?", "policy_id": "   "})
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400

    def test_top_k_not_int(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event({"query": "test?", "policy_id": _KNOWN_POLICY, "top_k": "abc"})
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400

    def test_top_k_out_of_range_zero(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event({"query": "test?", "policy_id": _KNOWN_POLICY, "top_k": 0})
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400

    def test_top_k_out_of_range_too_large(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event({"query": "test?", "policy_id": _KNOWN_POLICY, "top_k": 100})
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 400

    def test_body_too_large(self):
        import handler  # type: ignore[import-not-found]
        big = "x" * (9 * 1024)  # 9 KB > MAX_BODY_BYTES (8 KB)
        event = _make_event({"query": "test", "policy_id": _KNOWN_POLICY, "padding": big})
        resp = handler.handler(event, None)
        assert resp["statusCode"] in (400, 413)

    def test_value_error_from_answer_maps_to_400(self):
        """ValueError raised by answer() should map to 400, not 500."""
        import handler  # type: ignore[import-not-found]
        event = _make_event({"query": "test?", "policy_id": _KNOWN_POLICY})
        mock_answer = MagicMock(side_effect=ValueError("query too long"))
        with patch("handler._get_retriever"), \
             patch("handler._ensure_answer_loaded"), \
             patch.object(handler, "answer", mock_answer):
            resp = handler.handler(event, None)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert "query too long" in body["error"]


# ---------------------------------------------------------------------------
# Tests: 404 Unknown policy_id
# ---------------------------------------------------------------------------


class TestNotFound:
    def test_unknown_policy_id(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event({"query": "What data is collected?", "policy_id": _UNKNOWN_POLICY})
        resp = handler.handler(event, None)
        assert resp["statusCode"] == 404
        body = json.loads(resp["body"])
        assert "unknown" in body["error"].lower() or _UNKNOWN_POLICY in body["error"]

    def test_unknown_policy_does_not_call_answer(self):
        """404 check must short-circuit before any embedding/LLM call."""
        import handler  # type: ignore[import-not-found]
        mock_answer = MagicMock()
        with patch("handler._ensure_answer_loaded"), \
             patch.object(handler, "answer", mock_answer):
            event = _make_event(
                {"query": "What data is collected?", "policy_id": _UNKNOWN_POLICY}
            )
            resp = handler.handler(event, None)
        assert resp["statusCode"] == 404
        mock_answer.assert_not_called()

    def test_known_policy_passes_allowlist(self):
        """A known policy_id should not get a 404."""
        import handler  # type: ignore[import-not-found]
        event = _make_event({"query": "What data is collected?", "policy_id": _KNOWN_POLICY})
        with patch("handler._get_retriever"), \
             patch("handler._ensure_answer_loaded"), \
             patch.object(handler, "answer", MagicMock(return_value=_STUB_ANSWER)):
            resp = handler.handler(event, None)
        assert resp["statusCode"] != 404


# ---------------------------------------------------------------------------
# Tests: validation fires BEFORE answer() for all error cases
# ---------------------------------------------------------------------------


class TestValidationOrderGuarantee:
    """Prove that answer() is never called when validation fails."""

    def _assert_no_answer_call(self, event: dict[str, Any], expected_status: int) -> None:
        import handler  # type: ignore[import-not-found]
        mock_answer = MagicMock()
        with patch("handler._ensure_answer_loaded"), \
             patch.object(handler, "answer", mock_answer):
            resp = handler.handler(event, None)
        assert resp["statusCode"] == expected_status
        mock_answer.assert_not_called()

    def test_bad_json_no_answer(self):
        self._assert_no_answer_call(
            _make_event("not-json", raw=True), 400
        )

    def test_missing_query_no_answer(self):
        self._assert_no_answer_call(
            _make_event({"policy_id": _KNOWN_POLICY}), 400
        )

    def test_query_too_long_no_answer(self):
        self._assert_no_answer_call(
            _make_event({"query": "x" * 501, "policy_id": _KNOWN_POLICY}), 400
        )

    def test_unknown_policy_no_answer(self):
        self._assert_no_answer_call(
            _make_event({"query": "test?", "policy_id": _UNKNOWN_POLICY}), 404
        )


# ---------------------------------------------------------------------------
# Tests: 200 Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_200_structure(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event(
            {"query": "What data does Amazon collect?", "policy_id": _KNOWN_POLICY}
        )
        with patch("handler._get_retriever", return_value=MagicMock()), \
             patch("handler._ensure_answer_loaded"), \
             patch.object(handler, "answer", MagicMock(return_value=_STUB_ANSWER)):
            resp = handler.handler(event, None)

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])

        # Envelope fields
        assert "request_id" in body
        assert "latency_ms" in body
        assert "version" in body
        assert isinstance(body["latency_ms"], (int, float))

        # Answer fields (§3 schema)
        ans = body["answer"]
        assert "answerable" in ans
        assert "text" in ans
        assert "citations" in ans
        assert "policy_id" in ans
        assert "model" in ans

    def test_200_with_top_k(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event(
            {"query": "What data does Amazon collect?", "policy_id": _KNOWN_POLICY, "top_k": 3}
        )
        with patch("handler._get_retriever", return_value=MagicMock()), \
             patch("handler._ensure_answer_loaded"), \
             patch.object(handler, "answer", MagicMock(return_value=_STUB_ANSWER)):
            resp = handler.handler(event, None)
        assert resp["statusCode"] == 200

    def test_response_has_content_type(self):
        import handler  # type: ignore[import-not-found]
        event = _make_event(
            {"query": "What data does Amazon collect?", "policy_id": _KNOWN_POLICY}
        )
        with patch("handler._get_retriever", return_value=MagicMock()), \
             patch("handler._ensure_answer_loaded"), \
             patch.object(handler, "answer", MagicMock(return_value=_STUB_ANSWER)):
            resp = handler.handler(event, None)
        assert resp["headers"]["Content-Type"] == "application/json"
        assert "X-Request-Id" in resp["headers"]

    def test_500_on_unexpected_error(self):
        """Unexpected exceptions return 500 with safe body (no trace)."""
        import handler  # type: ignore[import-not-found]
        event = _make_event(
            {"query": "test?", "policy_id": _KNOWN_POLICY}
        )
        with patch("handler._get_retriever", side_effect=RuntimeError("boom")), \
             patch("handler._ensure_answer_loaded"):
            resp = handler.handler(event, None)
        assert resp["statusCode"] == 500
        body = json.loads(resp["body"])
        # 500 body must contain only error + request_id — no traceback
        assert set(body.keys()) == {"error", "request_id"}
        assert body["error"] == "Internal server error"
        assert "boom" not in body["error"]  # no internal details leaked


# ---------------------------------------------------------------------------
# Tests: gate.py unit tests
# ---------------------------------------------------------------------------


class TestGate:
    def _make_report(
        self,
        faith: float | None = 0.85,
        abst: float | None = 0.95,
        gate_passed: bool = True,
    ) -> dict:
        return {
            "n_items": 10,
            "backend": "chroma",
            "timestamp": "20260613T120000",
            "ragas": {
                "faithfulness": faith,
                "answer_relevancy": 0.90,
                "context_precision": 0.88,
                "context_recall": 0.82,
            },
            "house_metrics": {
                "abstention_accuracy": abst,
            },
            "gate_passed": gate_passed,
            "gate_failures": [],
        }

    def test_gate_passes_above_threshold(self):
        from eval.gate import check_gate
        report = self._make_report(faith=0.85, abst=0.95)
        passed, failures = check_gate(
            report,
            {"faithfulness": 0.80, "abstention_accuracy": 0.90},
        )
        assert passed
        assert failures == []

    def test_gate_fails_low_faithfulness(self):
        from eval.gate import check_gate
        report = self._make_report(faith=0.70, abst=0.95)
        passed, failures = check_gate(
            report,
            {"faithfulness": 0.80, "abstention_accuracy": 0.90},
        )
        assert not passed
        assert any("faithfulness" in f for f in failures)

    def test_gate_fails_low_abstention(self):
        from eval.gate import check_gate
        report = self._make_report(faith=0.85, abst=0.80)
        passed, failures = check_gate(
            report,
            {"faithfulness": 0.80, "abstention_accuracy": 0.90},
        )
        assert not passed
        assert any("abstention_accuracy" in f for f in failures)

    def test_gate_skips_nan_metric(self):
        """NaN (dry run / no API key) should not cause a failure."""
        from eval.gate import check_gate
        import math
        report = self._make_report(faith=float("nan"), abst=float("nan"))
        report["ragas"]["faithfulness"] = None  # JSON serialization of NaN
        report["house_metrics"]["abstention_accuracy"] = None
        passed, failures = check_gate(
            report,
            {"faithfulness": 0.80, "abstention_accuracy": 0.90},
        )
        assert passed
        assert failures == []

    def test_gate_faithfulness_override(self):
        """FAITHFULNESS_THRESHOLD env var / CLI flag overrides YAML threshold."""
        from eval.gate import check_gate
        report = self._make_report(faith=0.75, abst=0.95)
        # override to 0.70 — faith=0.75 should pass
        passed, failures = check_gate(
            report,
            {"faithfulness": 0.80, "abstention_accuracy": 0.90},
            faithfulness_override=0.70,
        )
        assert passed
        assert failures == []

    def test_gate_both_fail(self):
        from eval.gate import check_gate
        report = self._make_report(faith=0.50, abst=0.60)
        passed, failures = check_gate(
            report,
            {"faithfulness": 0.80, "abstention_accuracy": 0.90},
        )
        assert not passed
        assert len(failures) == 2
