"""Generation layer: answer() with citations and abstention.

Answer schema — see docs/CONTRACTS.md §3.
LangFuse tracing — see docs/CONTRACTS.md §8 and src/policylens/observability.py.
"""
from __future__ import annotations

import re
import time
from typing import TypedDict

import anthropic

from .config import Config
from .observability import RerankSpanData as _RerankSpanData  # re-exported for callers
from .observability import (
    TraceContext,
    trace_answer,
    update_trace_answer_metadata,
)
from .retrieve import RetrievedChunk, Retriever

# Re-export so api/handler.py can use without importing langfuse directly
__all__ = ["answer", "canned_answer", "Citation", "Answer", "TraceContext", "_RerankSpanData"]

ABSTENTION_TEXT = "The policy doesn't address this question."

# Input bounds — mirrors the API contract (docs/CONTRACTS.md §10: "query: string
# 1–500 chars"). Enforced here as defense-in-depth so answer() is safe regardless
# of caller; the API handler maps the resulting ValueError to a 400.
MAX_QUERY_CHARS = 500


def _validate_inputs(query: str, policy_id: str) -> None:
    """Reject malformed query/policy_id before any embedding or LLM call.

    Raises ValueError on bad input (handler → HTTP 400). This bounds per-request
    cost: an unbounded query would otherwise be embedded and sent to Claude.
    """
    if not isinstance(query, str) or not isinstance(policy_id, str):
        raise ValueError("query and policy_id must be strings")
    if not query.strip():
        raise ValueError("query must be a non-empty string")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"query exceeds {MAX_QUERY_CHARS} characters")
    if not policy_id.strip():
        raise ValueError("policy_id must be a non-empty string")

_SYSTEM_PROMPT = """\
You are a privacy-policy analyst. You answer questions about privacy policies \
using ONLY the numbered clauses provided below. Do not use any outside knowledge.

Rules:
1. Answer in plain English. Be concise (2-4 sentences). No legalese.
2. Every claim must be supported by a provided clause. Cite it as [N].
3. If the clauses do not support an answer, reply with exactly: UNANSWERABLE
4. Do not make up or infer information not stated in the clauses.\
"""

_USER_TEMPLATE = """\
Policy: {policy_id}
Question: {query}

Relevant clauses:
{context}

Answer (or UNANSWERABLE if not supported):\
"""


class Citation(TypedDict):
    chunk_id: str
    section: str
    quote: str


class Answer(TypedDict):
    answerable: bool
    text: str
    citations: list[Citation]
    policy_id: str
    model: str


def _truncate(text: str, max_chars: int = 500) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _short_quote(text: str, query: str, max_words: int = 25) -> str:
    """Extract a short supporting snippet from chunk text."""
    # Find the sentence most relevant to the query
    query_words = set(query.lower().split())
    sentences = re.split(r"(?<=[.!?])\s+", text)
    best = max(
        sentences,
        key=lambda s: len(query_words & set(s.lower().split())),
        default=sentences[0],
    )
    words = best.split()
    if len(words) <= max_words:
        return best
    return " ".join(words[:max_words]) + "…"


def _build_citations(
    answer_text: str,
    hits: list[RetrievedChunk],
    query: str,
) -> list[Citation]:
    """Build citations from chunks referenced in the answer via [N] markers."""
    citations: list[Citation] = []
    seen: set[str] = set()

    # Find all [N] references in the answer text
    refs = set(int(m.group(1)) for m in re.finditer(r"\[(\d+)\]", answer_text))

    for ref in sorted(refs):
        idx = ref - 1  # 1-indexed → 0-indexed
        if 0 <= idx < len(hits):
            hit = hits[idx]
            cid = hit["chunk"]["chunk_id"]
            if cid not in seen:
                seen.add(cid)
                citations.append(Citation(
                    chunk_id=cid,
                    section=hit["chunk"]["section"],
                    quote=_short_quote(hit["chunk"]["text"], query),
                ))

    # If model answered but cited nothing, cite the top hit as fallback
    if not citations and hits:
        top = hits[0]
        citations.append(Citation(
            chunk_id=top["chunk"]["chunk_id"],
            section=top["chunk"]["section"],
            quote=_short_quote(top["chunk"]["text"], query),
        ))

    return citations


def answer(
    query: str,
    policy_id: str,
    retriever: Retriever,
    cfg: Config,
) -> Answer:
    """Retrieve relevant clauses and generate a cited answer.

    Abstains (answerable=False) when no chunk scores above cfg.score_floor
    or when the LLM determines the context doesn't support an answer.

    Signature is frozen — see docs/CONTRACTS.md §3.
    Tracing is transparent: failures are swallowed so the answer is always returned.

    Raises ValueError on malformed input (empty/oversized query, non-string args);
    the API handler maps this to HTTP 400 before any cost is incurred.
    """
    _validate_inputs(query, policy_id)
    with trace_answer(query=query, policy_id=policy_id, cfg=cfg) as _obs:
        return _answer_impl(query, policy_id, retriever, cfg, _obs)


def _answer_impl(
    query: str,
    policy_id: str,
    retriever: Retriever,
    cfg: Config,
    obs: TraceContext,
) -> Answer:
    """Core answer logic; obs is a no-op TraceContext when tracing is disabled."""
    # --- Retrieval ---
    t0 = time.monotonic()
    hits = retriever.retrieve(query, policy_id, k=cfg.top_k)
    retrieve_ms = (time.monotonic() - t0) * 1000

    try:
        obs.record_retrieve(
            backend=cfg.retrieval_backend,
            k=cfg.top_k,
            candidate_count=len(hits),
            top_scores=[h["score"] for h in hits[:5]],
            policy_id=policy_id,
            latency_ms=retrieve_ms,
        )
    except Exception:
        pass  # tracing must never break the answer path

    # Pre-LLM abstention: no hits or all below score floor
    good_hits = [h for h in hits if h["score"] >= cfg.score_floor]
    if not good_hits:
        result = Answer(
            answerable=False,
            text=ABSTENTION_TEXT,
            citations=[],
            policy_id=policy_id,
            model=cfg.gen_model,
        )
        try:
            obs.record_generate(
                model=cfg.gen_model,
                input_tokens=0,
                output_tokens=0,
                abstention_path="score_floor",
                latency_ms=0.0,
            )
            update_trace_answer_metadata(obs, answerable=False, n_citations=0)
        except Exception:
            pass
        return result

    # Build numbered context block
    context_lines = []
    for i, hit in enumerate(good_hits, 1):
        chunk = hit["chunk"]
        context_lines.append(
            f"[{i}] ({chunk['section']}) {_truncate(chunk['text'])}"
        )
    context = "\n".join(context_lines)

    user_msg = _USER_TEMPLATE.format(
        policy_id=policy_id,
        query=query,
        context=context,
    )

    # --- Generation ---
    t1 = time.monotonic()
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=cfg.gen_model,
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    gen_ms = (time.monotonic() - t1) * 1000

    first_block = response.content[0]
    raw = first_block.text.strip() if hasattr(first_block, "text") else ""  # type: ignore[union-attr]
    input_tokens = response.usage.input_tokens if response.usage else 0
    output_tokens = response.usage.output_tokens if response.usage else 0

    # Post-LLM abstention
    if raw.upper().startswith("UNANSWERABLE") or raw.upper() == "UNANSWERABLE":
        result = Answer(
            answerable=False,
            text=ABSTENTION_TEXT,
            citations=[],
            policy_id=policy_id,
            model=cfg.gen_model,
        )
        try:
            obs.record_generate(
                model=cfg.gen_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                abstention_path="llm_unanswerable",
                latency_ms=gen_ms,
            )
            update_trace_answer_metadata(obs, answerable=False, n_citations=0)
        except Exception:
            pass
        return result

    citations = _build_citations(raw, good_hits, query)
    try:
        obs.record_generate(
            model=cfg.gen_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            abstention_path="none",
            latency_ms=gen_ms,
        )
        update_trace_answer_metadata(obs, answerable=True, n_citations=len(citations))
    except Exception:
        pass

    return Answer(
        answerable=True,
        text=raw,
        citations=citations,
        policy_id=policy_id,
        model=cfg.gen_model,
    )


def canned_answer(policy_id: str = "fixture_policy") -> Answer:
    """Return a hardcoded Answer for UI development before Phase 2 integration."""
    return Answer(
        answerable=True,
        text=(
            "According to the policy, the service may share data with advertising "
            "partners to deliver targeted ads [1]. Users can opt out via account settings."
        ),
        citations=[
            Citation(
                chunk_id=f"{policy_id}::data_sharing::c001",
                section="Data Sharing",
                quote="may share data with advertising partners to deliver targeted ads",
            )
        ],
        policy_id=policy_id,
        model="canned-stub",
    )
