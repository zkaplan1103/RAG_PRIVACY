---
tags: [generation]
---

# RAG-engineer memory

## 2026-06-01 | orchestrator (Phase 0 handoff)

- Implement `answer()` in `src/policylens/generate.py`.
- Signature: `answer(query, policy_id, retriever, cfg) -> Answer` — see docs/CONTRACTS.md §3.
- Use `FixtureRetriever` (in retrieve.py) for isolated dev; swap to ChromaRetriever in Phase 2.
- Abstain when all top-k scores < cfg.score_floor (0.30). Set `answerable=False`, `citations=[]`.
- Every sentence in a real answer must cite a chunk from the retriever results.
- `quote` field: <= 25 words, a short snippet from the chunk text supporting that claim.
- LLM call: Anthropic Python SDK, model from cfg.gen_model (`claude-haiku-4-5` dev).
- Abstention message: use the constant `ABSTENTION_TEXT` defined in generate.py.
- `model` field in Answer: set to the actual model string used (cfg.gen_model).
