"""LangFuse observability wrapper for PolicyLens.

One trace per answer() call; spans: retrieve / rerank / generate.
See docs/CONTRACTS.md §8 for the full metadata contract.

NO-OP GUARANTEE
---------------
If any of LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST is absent,
or Config.langfuse_enabled is False, this module makes zero network calls,
emits zero warnings, and has zero behavior impact on the caller.  The langfuse
SDK is imported lazily (inside _make_client) so that import-time safety is also
guaranteed — importing this module with missing keys is always safe.

INTEGRATION POINTS
------------------
generate.answer() wraps itself via the `trace_answer` context manager.
api/handler.py can attach span metadata via the `RerankSpanData` callback.

The rerank span is intentionally designed to be emitted only when rerank
metadata is provided.  The future PgVectorRetriever will call
`ctx.record_rerank(...)` inside the `with trace_answer(...)` block.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generator

from .config import Config

if TYPE_CHECKING:
    pass  # langfuse types only needed at runtime when keys are present

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cost table — input/output $/token for each model
# ---------------------------------------------------------------------------

# Prices as of 2026-06-11; update when Anthropic publishes new pricing.
# Keys are model IDs; values are (input_cost_per_token, output_cost_per_token).
_COST_PER_TOKEN: dict[str, tuple[float, float]] = {
    # claude-haiku-4-5
    "claude-haiku-4-5": (0.00000025, 0.00000125),
    # claude-sonnet-4-6
    "claude-sonnet-4-6": (0.000003, 0.000015),
    # claude-opus-4-8  (eval judge)
    "claude-opus-4-8": (0.000015, 0.000075),
    # Aliases / legacy names
    "claude-haiku-3-5": (0.00000025, 0.00000125),
    "claude-sonnet-3-5": (0.000003, 0.000015),
}


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated USD cost for a generation call.

    Falls back to zero (and logs a debug warning) for unknown models so that
    tracing never breaks the answer path.
    """
    rates = _COST_PER_TOKEN.get(model)
    if rates is None:
        logger.debug("observability: unknown model %r — cost set to 0.0", model)
        return 0.0
    in_rate, out_rate = rates
    return round(in_rate * input_tokens + out_rate * output_tokens, 10)


# ---------------------------------------------------------------------------
# App version (git SHA if available)
# ---------------------------------------------------------------------------

def _get_app_version() -> str:
    """Return the short git SHA of HEAD, or 'unknown'."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


_APP_VERSION: str | None = None  # lazily computed once


def _app_version() -> str:
    global _APP_VERSION
    if _APP_VERSION is None:
        _APP_VERSION = _get_app_version()
    return _APP_VERSION


# ---------------------------------------------------------------------------
# LangFuse client factory — lazy import, strict no-op on missing keys
# ---------------------------------------------------------------------------

def _keys_present() -> bool:
    """Return True iff all three LangFuse env vars are non-empty."""
    return all(
        os.environ.get(k, "").strip()
        for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")
    )


def _make_client(cfg: Config) -> Any | None:
    """Return a configured Langfuse client or None (no-op path).

    Langfuse SDK is imported here only — never at module level — so that
    importing observability.py with no keys set is always safe.
    """
    if not cfg.langfuse_enabled or not _keys_present():
        return None
    try:
        from langfuse import Langfuse  # noqa: PLC0415 — intentional lazy import

        return Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=os.environ["LANGFUSE_HOST"],
        )
    except Exception as exc:  # pragma: no cover — only hits on bad keys
        logger.debug("observability: failed to create LangFuse client: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Rerank metadata — passed in by PgVectorRetriever when reranking is active
# ---------------------------------------------------------------------------

@dataclass
class RerankSpanData:
    """Metadata for the optional rerank span (CONTRACTS §8).

    The future PgVectorRetriever calls ctx.record_rerank(RerankSpanData(...))
    inside the `with trace_answer(...)` block.  If this is never called, the
    rerank span is omitted entirely — consistent with the Chroma path.
    """
    model: str                              # e.g. "BAAI/bge-reranker-base"
    candidates_in: int                      # chunks before reranking
    candidates_out: int                     # chunks after reranking (top-n)
    score_deltas: list[float] = field(default_factory=list)  # per-item delta (optional)
    latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# Trace context — the object the caller holds inside `with trace_answer(...)`
# ---------------------------------------------------------------------------

class TraceContext:
    """Holds the live LangFuse trace + any in-flight span metadata.

    Callers interact through:
      - record_retrieve(...)  — called by generate.answer() after retrieval
      - record_rerank(...)    — called by PgVectorRetriever (optional)
      - record_generate(...)  — called by generate.answer() after LLM call
    All methods are no-ops when the client is None.
    """

    def __init__(self, client: Any | None, trace: Any | None) -> None:
        self._client = client
        self._trace = trace
        self._rerank_data: RerankSpanData | None = None

    # ---- public API --------------------------------------------------------

    def record_retrieve(
        self,
        *,
        backend: str,
        k: int,
        candidate_count: int,
        top_scores: list[float],
        policy_id: str,
        latency_ms: float,
    ) -> None:
        if self._trace is None:
            return
        try:
            self._trace.span(
                name="retrieve",
                metadata={
                    "backend": backend,
                    "k": k,
                    "candidate_count": candidate_count,
                    "top_scores": top_scores[:5],  # max 5 to keep payload small
                    "policy_id": policy_id,
                },
                input={"policy_id": policy_id, "k": k},
                output={"candidate_count": candidate_count, "top_scores": top_scores[:5]},
                start_time=None,  # langfuse SDK fills current time
                end_time=None,
            )
        except Exception as exc:
            logger.debug("observability: retrieve span error: %s", exc)

    def record_rerank(self, data: RerankSpanData) -> None:
        """Store rerank metadata; emitted as a span when the trace closes."""
        self._rerank_data = data

    def record_generate(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        abstention_path: str,  # "none" | "score_floor" | "llm_unanswerable"
        latency_ms: float,
    ) -> None:
        if self._trace is None:
            return
        cost = compute_cost(model, input_tokens, output_tokens)
        try:
            self._trace.generation(
                name="generate",
                model=model,
                usage={
                    "input": input_tokens,
                    "output": output_tokens,
                    "unit": "TOKENS",
                },
                metadata={
                    "cost_usd": cost,
                    "abstention_path": abstention_path,
                    "latency_ms": latency_ms,
                },
                input={"abstention_path": abstention_path},
                output={"input_tokens": input_tokens, "output_tokens": output_tokens},
            )
        except Exception as exc:
            logger.debug("observability: generate span error: %s", exc)

    # ---- internal ----------------------------------------------------------

    def _flush_rerank(self) -> None:
        """Emit the rerank span if data was provided."""
        if self._trace is None or self._rerank_data is None:
            return
        d = self._rerank_data
        try:
            self._trace.span(
                name="rerank",
                metadata={
                    "model": d.model,
                    "candidates_in": d.candidates_in,
                    "candidates_out": d.candidates_out,
                    "score_deltas": d.score_deltas,
                    "latency_ms": d.latency_ms,
                },
                input={"candidates_in": d.candidates_in},
                output={"candidates_out": d.candidates_out},
            )
        except Exception as exc:
            logger.debug("observability: rerank span error: %s", exc)


# ---------------------------------------------------------------------------
# Public context manager — wraps a single answer() call
# ---------------------------------------------------------------------------

@contextmanager
def trace_answer(
    *,
    query: str,
    policy_id: str,
    cfg: Config,
) -> Generator[TraceContext, None, None]:
    """Context manager that wraps a single answer() call with a LangFuse trace.

    Usage (inside generate.answer):

        with trace_answer(query=query, policy_id=policy_id, cfg=cfg) as ctx:
            # ... retrieval ...
            ctx.record_retrieve(backend=..., k=..., ...)
            # optional: reranker calls ctx.record_rerank(RerankSpanData(...))
            # ... generation ...
            ctx.record_generate(model=..., input_tokens=..., ...)

    When keys are absent or langfuse_enabled=False, the context manager
    yields a no-op TraceContext and never touches the network.
    """
    client = _make_client(cfg)
    trace: Any | None = None
    trace_ctx = TraceContext(client=client, trace=None)
    wall_start = time.monotonic()

    if client is not None:
        try:
            trace = client.trace(
                name="answer",
                metadata={
                    "policy_id": policy_id,
                    "gen_model": cfg.gen_model,
                    "score_floor": cfg.score_floor,
                    "top_k": cfg.top_k,
                    "app_version": _app_version(),
                },
                input={"query": query, "policy_id": policy_id},
            )
            trace_ctx = TraceContext(client=client, trace=trace)
        except Exception as exc:
            logger.debug("observability: failed to start trace: %s", exc)
            trace_ctx = TraceContext(client=None, trace=None)

    try:
        yield trace_ctx
    finally:
        # Emit the rerank span if populated
        trace_ctx._flush_rerank()
        # Update trace with final answer metadata (answerable, n_citations, latency)
        if trace is not None:
            latency_ms = (time.monotonic() - wall_start) * 1000
            try:
                trace.update(
                    metadata={
                        "latency_ms": round(latency_ms, 2),
                    }
                )
            except Exception as exc:
                logger.debug("observability: failed to update trace: %s", exc)
        # Flush — sdk queues in-process; flush ensures delivery before Lambda freezes
        if client is not None:
            try:
                client.flush()
            except Exception as exc:
                logger.debug("observability: flush error: %s", exc)


# ---------------------------------------------------------------------------
# Answer-level update helper (called after the Answer is built)
# ---------------------------------------------------------------------------

def update_trace_answer_metadata(
    ctx: TraceContext,
    *,
    answerable: bool,
    n_citations: int,
) -> None:
    """Attach answerable + n_citations to the open trace.

    Separate from the context manager so generate.answer() can call this
    after it has the Answer object in hand.
    """
    if ctx._trace is None:
        return
    try:
        ctx._trace.update(
            output={"answerable": answerable, "n_citations": n_citations},
            metadata={"answerable": answerable, "n_citations": n_citations},
        )
    except Exception as exc:
        logger.debug("observability: update_trace_answer_metadata error: %s", exc)
