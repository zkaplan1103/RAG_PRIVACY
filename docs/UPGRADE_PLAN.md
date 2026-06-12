# UPGRADE_PLAN.md — PolicyLens demo → production (v2)

> Phase-0 output, 2026-06-11. Orders the work, names the owning agent, and marks
> what is self-testable vs. what must be flagged in `SETUP_TASKS.md` because it
> needs live credentials. Interfaces live in `docs/CONTRACTS.md` (Part II).

## Target architecture (recap)

```
                       ┌────────────── CI (GitHub Actions) ──────────────┐
                       │ lint · pyright · pytest        promptfoo + Ragas │
                       │ (always)                       (needs secrets)   │
                       │                 faithfulness ≥ threshold gate    │
                       └──────────────────────────────────────────────────┘
 user ──▶ API Gateway ──▶ Lambda (container: handler + bge-small + reranker)
                              │ retrieve (hybrid: vector + FTS, RRF) ──▶ pgvector @ Supabase
                              │ rerank (bge-reranker-base, local)
                              │ generate + cite/abstain ──▶ Claude
                              └─ trace (spans, cost, latency) ──▶ LangFuse
 Streamlit app.py stays as a local demo client (Chroma or pgvector backend).
```

Key invariant: the frozen `Retriever` protocol and `Answer` schema (CONTRACTS
§2–3) do not change. pgvector/hybrid/rerank slot in *behind* `Retriever`;
structured outputs and both abstention paths are preserved and gated by evals.

## Phase ordering & dependencies

Ordered so everything locally verifiable lands before anything credential-bound.

| Phase | Work | Agent | Depends on | Self-test (local) | Flagged (needs user) |
|---|---|---|---|---|---|
| **P1** | Repo cleanup (stray `* 2.*` files) + Config v2 plumbing (new fields, backward-compatible defaults) | orchestrator | — | pytest, ruff, pyright | — |
| **P2** | Golden eval set v1 (150–200 Q/A from PrivacyQA + curated unanswerables, versioned + manifest), Ragas harness (`eval/ragas/`), promptfoo config, `eval/metrics.py` implemented | eval-engineer | P1 | schema/loader unit tests; promptfoo config dry-parse; harness runs against stub answers | full eval run (Anthropic spend) — flag |
| **P3** | LangFuse instrumentation: `observability.py` wrapper around `answer()` with spans (retrieve/rerank/generate), cost + latency metadata; strict no-op without keys | observability-engineer | P1 | unit tests prove no-op path + correct span payloads (mocked client) | live trace round-trip — flag |
| **P4** | pgvector migration: SQL schema + migration script, `PgVectorRetriever` (hybrid RRF: cosine + tsvector FTS), local cross-encoder reranker, backfill script Chroma→pgvector | vector-engineer | P1 | unit tests with fake DB cursor; if local Docker Postgres available, integration tests; reranker testable fully locally | Supabase connection test + backfill run — flag |
| **P5** | API + infra: `api/handler.py` (Lambda, container image), Terraform for Lambda + API Gateway + IAM, Dockerfile | infra-engineer | P4 (imports retriever) | handler unit tests (local invoke), `terraform fmt/validate`, docker build if available | `terraform apply`, image push — flag |
| **P6** | CI: GitHub Actions — lint/type/test always; eval job (promptfoo + Ragas) gated on secrets; **fail if faithfulness < threshold** (CONTRACTS §9) | infra-engineer | P2 | YAML lint; threshold logic unit-tested as a script | first real CI run with secrets — flag |
| **P7** | `SETUP_TASKS.md`, `TESTING_CHECKLIST.md`, README update | docs-engineer | all | review only | user executes SETUP_TASKS |

Parallelism: P2, P3, P4 are independent after P1 and can run as parallel
subagents (worktree isolation). P5 needs P4 merged; P6 needs P2 merged; P7 last.

## Architecture decisions (log mirrored in 00-decisions.md)

1. **Retriever protocol unchanged.** `PgVectorRetriever` implements the existing `Retriever`; `Config.retrieval_backend: "chroma" | "pgvector"` selects. Chroma remains the zero-credential dev path.
2. **Hybrid = RRF fusion** of pgvector cosine (HNSW) + Postgres full-text (`tsvector`/GIN), then optional cross-encoder rerank. Reranker is **local** (`BAAI/bge-reranker-base`) — no new external service, consistent with local-embeddings philosophy.
3. **Lambda ships as a container image** (torch + bge-small + reranker exceed zip limits). Cold-start cost is accepted for a demo-scale API; documented in TESTING_CHECKLIST.
4. **Ragas judge model: `claude-opus-4-8`** (configurable via `EVAL_JUDGE_MODEL`). Generation stays `claude-haiku-4-5` dev / `claude-sonnet-4-6` final.
5. **Regression gate: faithfulness ≥ 0.80** initially. Recalibrated from **baseline_v1** (decision 8) — the gate threshold is config, not code.
6. **Golden set provenance:** derived from PrivacyQA (MIT) mapped onto OPP-115 policies, plus hand-curated unanswerables. Versioned as `eval/golden/golden_v1.jsonl` + `MANIFEST.md`; never edited in place — changes create v2.
7. **Secrets posture:** all integrations read env vars (registry in CONTRACTS §11) and no-op/fall back when absent, so the repo is fully testable with zero accounts.
8. **baseline_v1 before pgvector cutover (user decision, 2026-06-11).** Before pgvector becomes the default retriever, the full eval suite runs against the **current Chroma retriever** (zero-cloud path — only `ANTHROPIC_API_KEY` needed) and the results are saved as `eval/baselines/baseline_v1.json`. This is an explicit, early step in SETUP_TASKS.md — not bundled into end-to-end testing. It anchors the regression comparison: pgvector cutover and every later CI run are measured against it. `Config.retrieval_backend` stays `"chroma"` until baseline_v1 exists.

## Flagged external steps (will appear in SETUP_TASKS.md)

- **Early — baseline_v1:** run the eval suite on the Chroma path (needs only `ANTHROPIC_API_KEY`) and save `eval/baselines/baseline_v1.json`, *before* the pgvector default switch (decision 8).
- Create Supabase project, enable pgvector, set `SUPABASE_DB_URL`; run migration + backfill.
- Create LangFuse project; set `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`.
- AWS account + credentials; `terraform init/plan/apply`; push container image.
- GitHub repo secrets (`ANTHROPIC_API_KEY`, Supabase, LangFuse) to activate the CI eval job.
- First full eval baseline run (Anthropic API spend) to calibrate the gate threshold.
