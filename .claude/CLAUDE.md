# CLAUDE.md — PolicyLens

Privacy-policy RAG with clause-level citations. Demo-grade, not production.
Full plan: `BUILD_PLAN.md`. Interfaces every agent must honor: `docs/CONTRACTS.md`.

## Golden rules
- **Honor `docs/CONTRACTS.md`.** Schemas and interface signatures there are frozen. If you must change one, update CONTRACTS first and log it in `docs/memory/00-decisions.md`.
- **Abstain over guess.** If retrieved clauses don't support an answer, say the policy doesn't address it. Never fabricate a citation. Every factual claim in an answer cites a real chunk id.
- **Cite the clause.** Each answer references the specific source chunk(s) it used (policy id + section + chunk id).
- **Respect data licenses.** OPP-115 is research/teaching use only. Raw data stays in `data/raw/` and is git-ignored. Cite sources in the README.

## Project layout
```
src/policylens/   ingest.py  index.py  retrieve.py  generate.py  config.py
app.py            Streamlit demo
data/raw/         downloaded corpora (git-ignored)
data/index/       cached embeddings + Chroma store (git-ignored)
eval/             eval seam only (Project C fills this in)
docs/             CONTRACTS.md, memory/
tests/            unit + the `make smoke` end-to-end check
```

## Commands
- Install: `uv sync`
- Build index: `uv run python -m policylens.index build`
- Run app: `uv run streamlit run app.py`
- Smoke test: `make smoke` (5 questions incl. one unanswerable)
- Lint/type: `uv run ruff check . && uv run pyright`

## Defaults (all overridable via Config — see CONTRACTS)
- Embeddings: local `bge-small-en-v1.5`. Generation: Claude (`haiku` dev / `sonnet` final).
- Vector store: Chroma persisted in `data/index/`.

## Working style (token discipline)
- Delegate heavy/isolated work to subagents; bring back only their final report.
- Recon with the `Explore` agent (Haiku, read-only). Build with Sonnet. Don't read datasets into the main thread.
- Never open large files (`chunks.jsonl`, PolicyQA). Sample with `head`, query with `jq`/`grep`, read counts.
- `/clear` between phases; `/compact` manually before auto-compact; `/rewind` on a wrong turn.
- Cache embeddings once to `data/index/`; don't recompute.

## Memory protocol
Before a task: read `docs/memory/INDEX.md`, then read ONLY files matching your tag(s).
After a task: append a dated entry to your tagged file + one line to `docs/memory/00-decisions.md`.

## Definition of done
Ask → retrieve → plain-English answer with citations → abstains when unanswerable → Streamlit demo runs → eval seam exists (not filled) → README written for a non-technical reader.
