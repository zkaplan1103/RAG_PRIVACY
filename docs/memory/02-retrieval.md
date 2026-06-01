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
