# CLAUDE.md — PolicyLens

Privacy-policy RAG with clause-level citations, being upgraded from a working
demo (Chroma + Streamlit) to a **production-grade, measured system**: versioned
golden evals (Ragas + promptfoo in CI), LangFuse tracing, pgvector on Supabase
with hybrid search + reranking, and AWS Lambda + API Gateway via Terraform.

Execution plan: `docs/UPGRADE_PLAN.md`. Frozen interfaces: `docs/CONTRACTS.md`
(v1 = shipped demo, v2 = production upgrade). v1 build history: `BUILD_PLAN.md`.

## Golden rules
- **Honor `docs/CONTRACTS.md`.** Schemas and signatures there are frozen. To change one: update CONTRACTS first, log it in `docs/memory/00-decisions.md`, then change code.
- **Abstain over guess.** If retrieved clauses don't support an answer, say the policy doesn't address it. Never fabricate a citation. Every factual claim cites a real chunk id. The upgrade must *preserve and harden* this — eval gates exist to prove it.
- **Cite the clause.** Each answer references the specific source chunk(s) used (policy id + section + chunk id).
- **Respect data licenses.** OPP-115 is research/teaching only; PrivacyQA is MIT. Raw data stays git-ignored in `data/raw/`. The golden eval set derives from PrivacyQA — keep provenance in its manifest.

## Production-upgrade constraints (binding)
- **Never create accounts, provision cloud resources, or obtain credentials** (AWS, Supabase, LangFuse). The user does that. Write all code/config assuming credentials arrive via env vars / GitHub secrets — see the env var registry in `docs/CONTRACTS.md` §11.
- **Implement fully, but do not execute** steps needing live external connections (terraform apply, pgvector connection tests, full eval-suite runs, LangFuse round-trips). Flag each in `SETUP_TASKS.md` with a verification note instead.
- **Degrade gracefully.** Every integration must no-op or fall back cleanly when its env vars are absent (LangFuse → no-op tracer; pgvector unset → Chroma; CI eval job skips without secrets).
- **Self-testable work first.** Prefer building what can be verified locally (unit tests, `terraform validate`, schema checks, stub runs) before anything needing live services.

## Project layout (target)
```
src/policylens/   ingest.py  index.py  retrieve.py  generate.py  config.py
                  observability.py (LangFuse wrapper)  pgvector.py (v2 retriever)
api/              Lambda handler (FastAPI/Mangum or plain handler)
app.py            Streamlit demo (kept as local client)
infra/            Terraform: Lambda, API Gateway, IAM (no state committed)
eval/             golden/ (versioned set + manifest)  ragas/  promptfoo/  metrics.py
.github/workflows CI: lint+type+test always; eval + regression gate when secrets exist
data/raw|index/   corpora + Chroma store (git-ignored)
docs/             CONTRACTS.md, UPGRADE_PLAN.md, memory/
tests/            unit + `make smoke`
SETUP_TASKS.md    every action the user must take, in order, with verification
TESTING_CHECKLIST.md  end-to-end verification once wired up
```

## Commands
- Install: `uv sync`  ·  Lint/type: `uv run ruff check . && uv run pyright`
- Tests: `uv run pytest tests/ -v`  ·  Smoke: `make smoke` (stub) / `make smoke-real`
- Build Chroma index: `uv run python -m policylens.index build`
- App: `uv run streamlit run app.py`
- Terraform (local only): `terraform -chdir=infra fmt -check && terraform -chdir=infra validate`

## Defaults (overridable via Config — see CONTRACTS)
- Embeddings: local `bge-small-en-v1.5` (384-dim). Reranker: local cross-encoder `BAAI/bge-reranker-base`.
- Generation: `claude-haiku-4-5` dev / `claude-sonnet-4-6` final. Eval judge (Ragas): `claude-opus-4-8`.
- Vector store: Chroma (`data/index/`) until pgvector cutover; both behind the frozen `Retriever` protocol, selected by `Config.retrieval_backend`.

## Agent roster (v2 — definitions in `.claude/agents/`)
| Agent | Does | Memory tag |
|---|---|---|
| `Explore` (built-in, haiku) | read-only recon | — |
| `eval-engineer` | golden set (150–200 Q/A), Ragas metrics, promptfoo config | `eval` |
| `vector-engineer` | pgvector migration, hybrid search, reranker | `retrieval` |
| `observability-engineer` | LangFuse tracing: cost, latency, spans | `observability` |
| `infra-engineer` | Lambda handler, Terraform, API Gateway, GitHub Actions + regression gate | `infra` |
| `docs-engineer` | SETUP_TASKS.md, TESTING_CHECKLIST.md, README | `decisions` (read-all) |

v1 builders (`data/index/rag/ui-engineer`, files at `.claude/` root) are retired; their work shipped.

## Working style (token discipline)
- Delegate heavy/isolated work to subagents; bring back only their final report. Build with Sonnet; recon with `Explore`.
- Never open large files (`chunks.jsonl`, PrivacyQA CSVs, `uv.lock`). Sample with `head`, query with `jq`/`grep`, read counts.
- `/clear` between phases; `/compact` manually before auto-compact; `/rewind` on a wrong turn.
- Cache embeddings once to `data/index/`; don't recompute.

## Memory protocol
Before a task: read `docs/memory/INDEX.md`, then read ONLY files matching your tag(s).
After a task: append a dated entry to your tagged file + one line to `docs/memory/00-decisions.md`.

## Definition of done (v2)
Demo behavior preserved (cited answers, abstention) → golden set versioned →
Ragas + promptfoo run in CI with a faithfulness regression gate → LangFuse
traces every answer (when keys present) → pgvector hybrid + rerank behind the
same Retriever protocol → `terraform validate` clean → SETUP_TASKS.md and
TESTING_CHECKLIST.md complete enough that the user can wire everything up
without asking questions.
