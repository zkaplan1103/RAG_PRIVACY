# PolicyLens — Claude Code orchestration layer (v2)

This folder is the orchestration layer for PolicyLens. The v1 demo (Chroma +
Streamlit, clause-level citations, abstention) shipped; the project is now
being upgraded to a production-grade, measured system.

## Read in this order
```
CLAUDE.md              ← loaded every session: rules, constraints, agent roster
docs/UPGRADE_PLAN.md   ← current plan: phases P1–P7, dependencies, what's flagged
docs/CONTRACTS.md      ← frozen interfaces (Part I = v1 demo, Part II = v2 upgrade)
docs/memory/INDEX.md   ← tag-based memory protocol
agents/                ← v2 subagents: eval, vector, observability, infra, docs
```

## v2 in one paragraph
Versioned golden eval set (150–200 Q/A) scored by Ragas + promptfoo in GitHub
Actions with a faithfulness regression gate; LangFuse tracing for cost/latency;
Chroma → pgvector on Supabase with hybrid search (RRF) + local cross-encoder
reranking behind the unchanged `Retriever` protocol; deployed as a Lambda
container behind API Gateway via Terraform. No accounts or cloud resources are
created by Claude — everything reads env vars (CONTRACTS §11) and degrades to
a no-op/local path without them; user-run steps land in `SETUP_TASKS.md`.

## Historical
`BUILD_PLAN.md` (v1 plan, shipped) and the root-level `data/index/rag/ui-engineer.md`
prompts are retired v1 artifacts, kept for the record.
