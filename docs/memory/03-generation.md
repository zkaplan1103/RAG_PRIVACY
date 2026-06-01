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

## 2026-06-01 | rag-engineer (Phase 1)
- Prompt shape: system instructs "answer from context only / cite [N] / say UNANSWERABLE if not supported"; user provides numbered clauses + question
- Two abstention paths: (1) pre-LLM score_floor check; (2) LLM returns "UNANSWERABLE"
- Citation extraction: parse [N] refs in answer text → look up hits[N-1]; fallback to top hit if no refs found
- Quote: most query-relevant sentence from chunk text, truncated to 25 words
- anthropic imported at module level so patch("src.policylens.generate.anthropic.Anthropic") works in tests
- 9/9 tests pass (all mocked, no API key required)
