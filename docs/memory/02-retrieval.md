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
