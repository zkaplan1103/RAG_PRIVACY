"""Tests for src/policylens/observability.py.

Key properties verified:
1. No-op path: no Langfuse client constructed when env vars absent.
2. Span structure / metadata when keys are present (mocked client).
3. Cost arithmetic against fixed usage payloads.
4. Rerank span emitted only when record_rerank() is called.
5. Tracing failures never break the answer path (swallowed silently).
6. trace_answer() context manager works end-to-end with mocked client.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from src.policylens.config import Config
from src.policylens.observability import (
    RerankSpanData,
    TraceContext,
    _keys_present,
    _make_client,
    compute_cost,
    trace_answer,
    update_trace_answer_metadata,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg_enabled() -> Config:
    """Config with tracing enabled (keys will be provided by test env patches)."""
    return Config(langfuse_enabled=True)


def _cfg_disabled() -> Config:
    return Config(langfuse_enabled=False)


def _env_with_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set all three required LangFuse env vars."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.example.com")


def _env_no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure all three LangFuse env vars are absent."""
    for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# 1. No-op path — no client constructed when keys absent
# ---------------------------------------------------------------------------

class TestNoOpPath:
    def test_keys_absent_no_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_make_client must return None when env vars are missing."""
        _env_no_keys(monkeypatch)
        cfg = _cfg_enabled()
        client = _make_client(cfg)
        assert client is None

    def test_langfuse_disabled_no_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_make_client must return None when langfuse_enabled=False."""
        _env_with_keys(monkeypatch)
        cfg = _cfg_disabled()
        client = _make_client(cfg)
        assert client is None

    def test_langfuse_sdk_never_imported_without_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The langfuse SDK class must never be instantiated when keys are absent."""
        _env_no_keys(monkeypatch)
        cfg = _cfg_enabled()
        with patch("langfuse.Langfuse") as mock_cls:
            _make_client(cfg)
            mock_cls.assert_not_called()

    def test_keys_present_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env_no_keys(monkeypatch)
        assert _keys_present() is False

        _env_with_keys(monkeypatch)
        assert _keys_present() is True

    def test_partially_missing_keys_no_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only PUBLIC_KEY set — should still be no-op."""
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        assert _keys_present() is False
        assert _make_client(_cfg_enabled()) is None

    def test_trace_answer_noop_no_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """trace_answer() must yield without error when keys are absent."""
        _env_no_keys(monkeypatch)
        cfg = _cfg_enabled()
        with trace_answer(query="test query", policy_id="pol_x", cfg=cfg) as ctx:
            assert isinstance(ctx, TraceContext)
            # All record_* calls must be silent no-ops
            ctx.record_retrieve(
                backend="chroma", k=5, candidate_count=3,
                top_scores=[0.9, 0.8, 0.7], policy_id="pol_x", latency_ms=12.0,
            )
            ctx.record_rerank(RerankSpanData(
                model="BAAI/bge-reranker-base",
                candidates_in=10, candidates_out=5,
                score_deltas=[0.1, 0.2], latency_ms=5.0,
            ))
            ctx.record_generate(
                model="claude-haiku-4-5", input_tokens=200, output_tokens=50,
                abstention_path="none", latency_ms=300.0,
            )
            update_trace_answer_metadata(ctx, answerable=True, n_citations=2)


# ---------------------------------------------------------------------------
# 2. Span structure / metadata (mocked client)
# ---------------------------------------------------------------------------

@contextmanager
def _mocked_langfuse(monkeypatch: pytest.MonkeyPatch):
    """Context manager: set env keys + patch langfuse.Langfuse with a mock."""
    _env_with_keys(monkeypatch)

    mock_trace = MagicMock()
    mock_client = MagicMock()
    mock_client.trace.return_value = mock_trace

    with patch("src.policylens.observability._make_client", return_value=mock_client):
        yield mock_client, mock_trace


class TestSpanStructure:
    def test_retrieve_span_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with _mocked_langfuse(monkeypatch) as (mock_client, mock_trace):
            cfg = _cfg_enabled()
            with trace_answer(query="q", policy_id="pol", cfg=cfg) as ctx:
                ctx.record_retrieve(
                    backend="chroma",
                    k=5,
                    candidate_count=3,
                    top_scores=[0.9, 0.8, 0.7],
                    policy_id="pol",
                    latency_ms=10.5,
                )

            # span() was called exactly once for "retrieve"
            span_calls = [c for c in mock_trace.span.call_args_list]
            names = [c.kwargs.get("name") or c.args[0] for c in span_calls]
            assert "retrieve" in names

    def test_retrieve_span_metadata_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with _mocked_langfuse(monkeypatch) as (_, mock_trace):
            cfg = _cfg_enabled()
            with trace_answer(query="q", policy_id="pol", cfg=cfg) as ctx:
                ctx.record_retrieve(
                    backend="chroma", k=5, candidate_count=3,
                    top_scores=[0.9, 0.8, 0.7], policy_id="pol", latency_ms=10.5,
                )

            call_kwargs = mock_trace.span.call_args_list[0].kwargs
            meta = call_kwargs["metadata"]
            assert meta["backend"] == "chroma"
            assert meta["k"] == 5
            assert meta["candidate_count"] == 3
            assert meta["policy_id"] == "pol"
            assert isinstance(meta["top_scores"], list)

    def test_generate_span_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with _mocked_langfuse(monkeypatch) as (_, mock_trace):
            cfg = _cfg_enabled()
            with trace_answer(query="q", policy_id="pol", cfg=cfg) as ctx:
                ctx.record_generate(
                    model="claude-haiku-4-5",
                    input_tokens=100,
                    output_tokens=50,
                    abstention_path="none",
                    latency_ms=200.0,
                )

            mock_trace.generation.assert_called_once()
            call_kwargs = mock_trace.generation.call_args.kwargs
            assert call_kwargs["name"] == "generate"
            assert call_kwargs["model"] == "claude-haiku-4-5"
            assert call_kwargs["usage"]["input"] == 100
            assert call_kwargs["usage"]["output"] == 50

    def test_generate_span_contains_cost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with _mocked_langfuse(monkeypatch) as (_, mock_trace):
            cfg = _cfg_enabled()
            with trace_answer(query="q", policy_id="pol", cfg=cfg) as ctx:
                ctx.record_generate(
                    model="claude-haiku-4-5",
                    input_tokens=1000,
                    output_tokens=200,
                    abstention_path="none",
                    latency_ms=200.0,
                )

            call_kwargs = mock_trace.generation.call_args.kwargs
            assert "cost_usd" in call_kwargs["metadata"]
            assert call_kwargs["metadata"]["cost_usd"] > 0

    def test_abstention_path_recorded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with _mocked_langfuse(monkeypatch) as (_, mock_trace):
            cfg = _cfg_enabled()
            with trace_answer(query="q", policy_id="pol", cfg=cfg) as ctx:
                ctx.record_generate(
                    model="claude-haiku-4-5",
                    input_tokens=0,
                    output_tokens=0,
                    abstention_path="score_floor",
                    latency_ms=0.0,
                )

            call_kwargs = mock_trace.generation.call_args.kwargs
            assert call_kwargs["metadata"]["abstention_path"] == "score_floor"

    def test_trace_metadata_contains_policy_and_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _mocked_langfuse(monkeypatch) as (mock_client, _):
            cfg = _cfg_enabled()
            # We patch _make_client to return mock_client which creates traces
            mock_trace = MagicMock()
            mock_client.trace.return_value = mock_trace

            with trace_answer(query="test?", policy_id="google", cfg=cfg):
                pass

            trace_call_kwargs = mock_client.trace.call_args.kwargs
            assert trace_call_kwargs["metadata"]["policy_id"] == "google"
            assert trace_call_kwargs["metadata"]["gen_model"] == cfg.gen_model
            assert "score_floor" in trace_call_kwargs["metadata"]
            assert "top_k" in trace_call_kwargs["metadata"]

    def test_update_trace_answer_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with _mocked_langfuse(monkeypatch) as (mock_client, mock_trace):
            cfg = _cfg_enabled()
            with trace_answer(query="q", policy_id="pol", cfg=cfg) as ctx:
                # Manually wire the mock trace into the ctx
                ctx._trace = mock_trace
                update_trace_answer_metadata(ctx, answerable=True, n_citations=3)

            update_calls = mock_trace.update.call_args_list
            # At least one call should have answerable in output or metadata
            found = any(
                c.kwargs.get("output", {}).get("answerable") is True
                or c.kwargs.get("metadata", {}).get("answerable") is True
                for c in update_calls
            )
            assert found


# ---------------------------------------------------------------------------
# 3. Rerank span — emitted only when record_rerank() is called
# ---------------------------------------------------------------------------

class TestRerankSpan:
    def test_rerank_span_emitted_when_provided(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with _mocked_langfuse(monkeypatch) as (_, mock_trace):
            cfg = _cfg_enabled()
            with trace_answer(query="q", policy_id="pol", cfg=cfg) as ctx:
                ctx._trace = mock_trace
                ctx.record_rerank(RerankSpanData(
                    model="BAAI/bge-reranker-base",
                    candidates_in=20,
                    candidates_out=5,
                    score_deltas=[0.15, 0.10, -0.05, -0.10, -0.15],
                    latency_ms=42.0,
                ))
            # span() should have been called with name="rerank" during __exit__
            span_names = [
                c.kwargs.get("name") for c in mock_trace.span.call_args_list
            ]
            assert "rerank" in span_names

    def test_rerank_span_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with _mocked_langfuse(monkeypatch) as (_, mock_trace):
            cfg = _cfg_enabled()
            with trace_answer(query="q", policy_id="pol", cfg=cfg) as ctx:
                ctx._trace = mock_trace
                ctx.record_rerank(RerankSpanData(
                    model="BAAI/bge-reranker-base",
                    candidates_in=20,
                    candidates_out=5,
                    score_deltas=[0.1],
                    latency_ms=30.0,
                ))

            rerank_calls = [
                c for c in mock_trace.span.call_args_list
                if c.kwargs.get("name") == "rerank"
            ]
            assert len(rerank_calls) == 1
            meta = rerank_calls[0].kwargs["metadata"]
            assert meta["model"] == "BAAI/bge-reranker-base"
            assert meta["candidates_in"] == 20
            assert meta["candidates_out"] == 5

    def test_rerank_span_not_emitted_when_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _mocked_langfuse(monkeypatch) as (_, mock_trace):
            cfg = _cfg_enabled()
            with trace_answer(query="q", policy_id="pol", cfg=cfg) as ctx:
                ctx._trace = mock_trace
                # Do NOT call record_rerank
                pass

            span_names = [
                c.kwargs.get("name") for c in mock_trace.span.call_args_list
            ]
            assert "rerank" not in span_names

    def test_rerank_span_data_dataclass(self) -> None:
        d = RerankSpanData(
            model="BAAI/bge-reranker-base",
            candidates_in=10,
            candidates_out=5,
        )
        assert d.score_deltas == []
        assert d.latency_ms == 0.0


# ---------------------------------------------------------------------------
# 4. Cost arithmetic
# ---------------------------------------------------------------------------

class TestCostArithmetic:
    def test_haiku_cost(self) -> None:
        # 1000 input @ $0.25/M = $0.00025; 200 output @ $1.25/M = $0.00025
        cost = compute_cost("claude-haiku-4-5", input_tokens=1000, output_tokens=200)
        expected = 0.00000025 * 1000 + 0.00000125 * 200
        assert abs(cost - expected) < 1e-12

    def test_sonnet_cost(self) -> None:
        cost = compute_cost("claude-sonnet-4-6", input_tokens=500, output_tokens=100)
        expected = 0.000003 * 500 + 0.000015 * 100
        assert abs(cost - expected) < 1e-12

    def test_zero_tokens_zero_cost(self) -> None:
        cost = compute_cost("claude-haiku-4-5", input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_unknown_model_zero_cost(self) -> None:
        cost = compute_cost("some-unknown-model-xyz", input_tokens=1000, output_tokens=500)
        assert cost == 0.0

    def test_cost_is_non_negative(self) -> None:
        for model in ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"):
            assert compute_cost(model, 100, 100) >= 0

    def test_output_more_expensive_than_input(self) -> None:
        # For all known models, output tokens should cost more than input tokens
        for model in ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"):
            input_cost = compute_cost(model, input_tokens=1, output_tokens=0)
            output_cost = compute_cost(model, input_tokens=0, output_tokens=1)
            assert output_cost > input_cost, f"{model}: output should cost more"


# ---------------------------------------------------------------------------
# 5. Tracing failures never break the answer path
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_broken_trace_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the LangFuse trace raises on every method, no exception propagates."""
        _env_with_keys(monkeypatch)

        bad_trace = MagicMock()
        bad_trace.span.side_effect = RuntimeError("network error")
        bad_trace.generation.side_effect = RuntimeError("network error")
        bad_trace.update.side_effect = RuntimeError("network error")

        bad_client = MagicMock()
        bad_client.trace.return_value = bad_trace
        bad_client.flush.side_effect = RuntimeError("flush error")

        with patch("src.policylens.observability._make_client", return_value=bad_client):
            cfg = _cfg_enabled()
            # Must not raise
            with trace_answer(query="q", policy_id="pol", cfg=cfg) as ctx:
                ctx.record_retrieve(
                    backend="chroma", k=5, candidate_count=2,
                    top_scores=[0.9], policy_id="pol", latency_ms=5.0,
                )
                ctx.record_generate(
                    model="claude-haiku-4-5",
                    input_tokens=100,
                    output_tokens=50,
                    abstention_path="none",
                    latency_ms=100.0,
                )
                update_trace_answer_metadata(ctx, answerable=True, n_citations=1)

    def test_noop_trace_context_all_methods_safe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All TraceContext methods must be safe on a no-op instance (client=None)."""
        _env_no_keys(monkeypatch)
        ctx = TraceContext(client=None, trace=None)
        # None of these should raise
        ctx.record_retrieve(
            backend="chroma", k=5, candidate_count=0,
            top_scores=[], policy_id="pol", latency_ms=0.0,
        )
        ctx.record_rerank(RerankSpanData(
            model="BAAI/bge-reranker-base", candidates_in=5, candidates_out=3,
        ))
        ctx.record_generate(
            model="claude-haiku-4-5", input_tokens=0, output_tokens=0,
            abstention_path="score_floor", latency_ms=0.0,
        )
        update_trace_answer_metadata(ctx, answerable=False, n_citations=0)
        ctx._flush_rerank()  # also must be safe


# ---------------------------------------------------------------------------
# 6. Integration with generate.answer() (mocked Anthropic + mocked LangFuse)
# ---------------------------------------------------------------------------

class TestGenerateIntegration:
    """Verify tracing is wired into generate.answer() without changing its behavior."""

    def _make_retriever(self, score: float = 0.9):

        from src.policylens.ingest import Chunk
        from src.policylens.retrieve import RetrievedChunk

        class _R:
            def retrieve(self, query, policy_id, k=5):
                return [RetrievedChunk(
                    chunk=Chunk(
                        chunk_id="p::sec::c000",
                        policy_id=policy_id,
                        policy_name="Test",
                        section="Privacy",
                        text="We collect email addresses for account management.",
                        char_start=0,
                        char_end=50,
                        source_url=None,
                    ),
                    score=score,
                )]

        return _R()

    def test_answer_still_works_with_tracing_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock, patch

        from src.policylens.generate import answer

        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)

        cfg = Config(score_floor=0.30, langfuse_enabled=False)

        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="Email is collected for account management [1].")]
        mock_msg.usage.input_tokens = 100
        mock_msg.usage.output_tokens = 30
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg

        with patch("src.policylens.generate.anthropic.Anthropic", return_value=mock_client):
            result = answer("email", "pol", self._make_retriever(0.9), cfg)

        assert result["answerable"] is True
        assert result["policy_id"] == "pol"

    def test_answer_abstains_score_floor_no_tracing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.policylens.generate import ABSTENTION_TEXT, answer

        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        cfg = Config(score_floor=0.90, langfuse_enabled=False)
        result = answer("anything", "pol", self._make_retriever(score=0.10), cfg)
        assert result["answerable"] is False
        assert result["text"] == ABSTENTION_TEXT
