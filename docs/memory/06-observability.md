# Observability notes — tag: `observability` (observability-engineer)

Append-only, dated. LangFuse tracing: trace/span design, cost capture, no-op fallback.

---

2026-06-11 | orchestrator | File created in Phase 0 of the production upgrade. Contract: docs/CONTRACTS.md §8. No work yet.

## 2026-06-12 | P3 complete: LangFuse tracing layer

### Files created/changed
- `src/policylens/observability.py` — new; thin tracing module (see below)
- `src/policylens/generate.py` — instrumented with `trace_answer()` context manager;
  frozen signature unchanged; also fixed pre-existing pyright error on `content[0].text`
- `src/policylens/config.py` — added Config v2 fields (CONTRACTS §6):
  `retrieval_backend`, `db_url_env`, `hybrid_rrf_k`, `fts_candidates`,
  `rerank_enabled`, `rerank_model`, `rerank_top_n`, `judge_model`, `langfuse_enabled`
- `tests/test_observability.py` — new; 27 tests covering all four guarantees
- `pyproject.toml` — `langfuse>=2.0.0,<3.0.0` added to main deps

### Trace shape
One trace per `answer()` call (name: `"answer"`). Trace metadata: `policy_id`,
`gen_model`, `score_floor`, `top_k`, `app_version` (git SHA), `latency_ms`,
`answerable`, `n_citations`.

Spans:
- `retrieve`: backend, k, candidate_count, top_scores (capped at 5), policy_id,
  latency_ms
- `rerank`: model, candidates_in, candidates_out, score_deltas, latency_ms —
  **only emitted when** `ctx.record_rerank(RerankSpanData(...))` is called; absent
  on the Chroma path
- `generate` (LangFuse `generation` type): model, input_tokens, output_tokens (from
  Anthropic `usage`), cost_usd (computed from `_COST_PER_TOKEN` table),
  abstention_path (`"none"` | `"score_floor"` | `"llm_unanswerable"`), latency_ms

### No-op guarantee
- `_keys_present()` checks all three vars at call time (no module-level check)
- `langfuse.Langfuse` imported only inside `_make_client()` (lazy import)
- With missing keys or `langfuse_enabled=False`: client=None, all span/trace methods
  on TraceContext are early-return no-ops, no warnings, no network calls
- Verified by `env -i` clean-environment smoke test + 6 dedicated unit tests

### Rerank hook for PgVectorRetriever
`RerankSpanData` dataclass + `ctx.record_rerank(data)` is the contract.
PgVectorRetriever calls this inside the `with trace_answer(...)` block (which it
receives from generate._answer_impl's obs parameter). No pgvector import in
observability.py.

### Cost table
Models in `_COST_PER_TOKEN`: `claude-haiku-4-5`, `claude-sonnet-4-6`,
`claude-opus-4-8` plus aliases. Unknown models return 0.0 and debug-log.

### Test results
27 new tests + 24 pre-existing = 51 passed. ruff clean. pyright clean on new files.
