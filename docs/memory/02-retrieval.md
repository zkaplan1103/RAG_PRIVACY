---
tags: [retrieval]
---

# Index-engineer memory

## 2026-06-01 | orchestrator (Phase 0 handoff)

- Input: `data/processed/chunks.jsonl` (or `tests/fixtures/chunks_sample.jsonl` for isolated dev).
- Embedding model: `BAAI/bge-small-en-v1.5` via sentence-transformers. Local, no API key.
- Vector store: Chroma persisted to `data/index/`. Collection name: `policylens`.
- Retrieval is scoped per `policy_id` — filter on `policy_id` metadata field in Chroma.
- Returns list[RetrievedChunk] sorted by cosine score desc, at most k=5 (cfg.top_k).
- score_floor=0.30: callers (generate.py) use this threshold; retriever just returns scores.
- Must pass tests against the 10-row fixture in `tests/fixtures/chunks_sample.jsonl`.
- ChromaRetriever class lives in `src/policylens/retrieve.py` — fill in the `__init__` and `retrieve` methods.

## 2026-06-01 | index-engineer (Phase 1)
- Embed model: BAAI/bge-small-en-v1.5 (sentence-transformers 2.7.x), dims: 384
- Index: Chroma PersistentClient, collection "policylens", hnsw:space=cosine
- Score: `1.0 - cosine_distance` (Chroma returns cosine distance ∈ [0,2])
- Cache: `collection.count() > 0` check skips re-embedding on repeat builds
- numpy pinned to <2.0 (torch 2.2 ABI incompatibility with numpy 2.x)
- 8/8 retrieval tests pass on fixture (26s, includes embedding 10 chunks)

## 2026-06-12 | vector-engineer (v2: pgvector hybrid + rerank)
- New: `infra/sql/001_init.sql` — idempotent pgvector schema (CONTRACTS §7 exact):
  vector(384), generated tsvector STORED, HNSW + GIN + policy_id B-tree indexes.
- New: `src/policylens/pgvector.py` — `PgVectorRetriever` implementing frozen Retriever protocol.
  Hybrid: cosine ANN + FTS both scoped to policy_id, fused via RRF (`1/(rrf_k+rank)`
  summed across legs). Optional cross-encoder rerank with `BAAI/bge-reranker-base`;
  reranker logits rescaled linearly to [0,1] so score_floor abstention keeps working.
  psycopg v3 ConnectionPool; DSN from os.environ[cfg.db_url_env] (fail-fast if absent).
  Pool injection point `_pool` for unit tests (no DB needed).
- New: `src/policylens/migrate_pgvector.py` — idempotent batched upsert backfill.
  Loads Chroma embeddings when present (avoids re-embedding 2393 chunks); embeds fresh
  otherwise. `_conn` injection point for unit tests.
- Modified: `src/policylens/retrieve.py` — added `make_retriever(cfg)` factory:
  "chroma" (default) → ChromaRetriever, "pgvector" → PgVectorRetriever. Chroma path
  unchanged. Applied upstream type fixes (np.asarray, cast for chroma metadata).
- Modified: `tests/smoke.py` — real mode uses `make_retriever()` (backend via Config).
- Modified: `app.py` — uses `make_retriever(DEFAULT_CONFIG)` for backend dispatch.
- New: `tests/test_pgvector.py` — 32 unit tests (faked pool/cursor, no DB required).
  Real reranker test skips gracefully when model unavailable (disk full in CI).
  Docker integration test (pytest -m pgvector) skips when daemon not running.
- New: `pytest.ini` — registers pgvector marker.
- psycopg[binary,pool]>=3.3.4 added to main deps.
- RRF design: score(d) = Σ_leg 1/(rrf_k+rank). Default rrf_k=60 gives 0.016 max per leg;
  two-leg hit ≈ 0.033. Rescaled to [0,1] for score_floor. Cross-encoder overrides RRF score.
- Rescaling: linear min-max. All-equal input → all 1.0 (avoids divide-by-zero).
- test_abstain_model_says_unanswerable fails in both main and worktree — pre-existing.
