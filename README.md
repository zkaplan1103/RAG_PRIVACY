# PolicyLens

> Ask plain-English questions about privacy policies. Every answer cites the exact clause it used — or tells you the policy doesn't address it.

Privacy policies are long, dense, and written by lawyers. PolicyLens lets you
ask a normal question ("Does this app share my data with advertisers?") and get
a short, honest answer that points directly to the clause that supports it — or
says "the policy doesn't address this" when the answer isn't there.

---

## Demo

1. Pick a policy from the sidebar (Amazon, AOL, Honda, and others from the
   OPP-115 corpus)
2. Type a question or click an example
3. Get a plain-English answer with expandable citation cards showing the exact
   clause

If no relevant clause scores above the confidence threshold, the app says so —
it never makes something up.

---

## How it works

PolicyLens is a **RAG** (Retrieval-Augmented Generation) system with three
stages:

**1. Ingest** — 115 real website privacy policies from the
[OPP-115 corpus](https://usableprivacy.org/data) are parsed into 2,393
clause-level chunks (~150–400 words each), labeled with their data-practice
category (e.g. "Third Party Sharing", "Data Security").

**2. Retrieve** — When you ask a question, it's embedded using a local
sentence-transformer model (`bge-small-en-v1.5`) and compared against every
chunk for the selected policy. In demo mode, retrieval uses Chroma (local,
zero-credential). In production, it uses hybrid search on Supabase pgvector
(vector cosine + full-text search, fused via Reciprocal Rank Fusion), followed
by a local cross-encoder reranker (`bge-reranker-base`). The top-5 most
relevant clauses are returned.

**3. Generate** — Those clauses — and only those clauses — are handed to
Claude (Haiku for dev, Sonnet for production). The prompt explicitly instructs
the model not to use outside knowledge. It either answers with inline citations
(`[1]`, `[2]`) or says "UNANSWERABLE" if the clauses don't support a response.

The citations you see in the UI are traceable to real chunk IDs and real text
from the source document.

---

## Quickstart (local demo)

**Requirements:** Python 3.11+, [`uv`](https://github.com/astral-sh/uv), an
[Anthropic API key](https://console.anthropic.com)

```bash
# 1. Install dependencies
uv sync

# 2. Build the vector index (one-time, ~5 min on CPU)
make index

# 3. Run the app
export ANTHROPIC_API_KEY=sk-ant-...
uv run streamlit run app.py
```

The index is cached to `data/index/` — rebuilding is skipped on subsequent
runs.

---

## Project layout

```
src/policylens/
  config.py          shared Config dataclass (v1 + v2 fields)
  ingest.py          parse OPP-115 HTML → chunks.jsonl
  index.py           embed chunks → Chroma vector store
  retrieve.py        ChromaRetriever + PgVectorRetriever + make_retriever() factory
  pgvector.py        PgVectorRetriever: hybrid RRF + bge-reranker-base (v2)
  migrate_pgvector.py  idempotent backfill: Chroma → Supabase pgvector
  generate.py        answer() with citations and two abstention paths
  observability.py   LangFuse wrapper (no-op when keys absent)
api/
  handler.py         Lambda handler: thin adapter, full input validation
  authorizer.py      Lambda authorizer: x-api-key enforcement, fails closed
  Dockerfile         Lambda container image (~1.8 GB, bakes in HF models)
infra/
  main.tf            ECR, Lambda, API Gateway, CloudWatch, Secrets Manager, IAM
  variables.tf       variable declarations
  outputs.tf         api_endpoint, ecr_repository_url
  terraform.tfvars.example  copy → terraform.tfvars and fill in values
  sql/001_init.sql   pgvector schema (CREATE TABLE chunks + indexes)
  SETUP_NOTES.md     detailed flagged steps (referenced by SETUP_TASKS.md)
eval/
  golden/golden_v1.jsonl   167 golden Q/A items (137 answerable, 30 unanswerable)
  golden/MANIFEST.md        provenance, license notes, curation rules
  golden/build_golden.py    reproducible build script for the golden set
  ragas/run_ragas.py        Ragas harness (--backend chroma|pgvector, --out PATH)
  promptfoo/promptfooconfig.yaml  promptfoo provider + assertions
  metrics.py                evaluate(): abstention/citation metrics
  gate.py                   CI regression gate script
  thresholds.yaml           faithfulness: 0.80, abstention_accuracy: 0.90
  baselines/baseline_v1.json  (to be created by user — see SETUP_TASKS.md)
.github/workflows/ci.yml    two-job CI: checks (always) + eval (gated on secrets)
app.py                      Streamlit demo (local client, Chroma or pgvector)
tests/                      157 unit tests; fixtures/chunks_sample.jsonl
data/raw/                   OPP-115 + PrivacyQA corpora (git-ignored)
data/index/                 Chroma vector store (git-ignored)
docs/
  CONTRACTS.md      frozen interfaces (v1 + v2)
  UPGRADE_PLAN.md   production upgrade plan and architecture decisions
  memory/           agent memory files (append-only build log)
SETUP_TASKS.md      step-by-step wiring guide for the user
TESTING_CHECKLIST.md  end-to-end verification once wired up
```

---

## Running tests

```bash
uv run pytest tests/ -v             # 157 unit tests, no API key needed
make smoke                          # 5-question stub smoke test
make smoke-real                     # end-to-end with real LLM (requires API key)
uv run ruff check .                 # lint (0 errors)
uv run pyright                      # type check (0 errors)
terraform -chdir=infra validate     # Terraform config (requires terraform >= 1.6)
```

---

## Production architecture

```
                   ┌──────────────────── CI (GitHub Actions) ─────────────────────┐
                   │ lint · pyright · pytest (checks — always, no secrets needed)  │
                   │ Ragas + promptfoo + faithfulness gate (eval — needs API key)   │
                   └────────────────────────────────────────────────────────────────┘

client request
      │
      ▼
API Gateway (HTTP API v2)
      │  POST /ask  ──▶  Lambda authorizer (api/authorizer.py)
      │                  x-api-key header check vs. Secrets Manager
      │                  fails closed (403) if key absent or wrong
      │
      ▼
Lambda function (api/handler.py — container image)
  1. Validate body: size, JSON, types, query length (1–500 chars)
  2. Check policy_id against allowlist → 404 BEFORE any LLM cost
  3. Call answer() from policylens.generate
      │
      ├──▶ retrieve (src/policylens/pgvector.py)
      │      cosine ANN (HNSW) + full-text (tsvector/GIN), fused by RRF
      │      cross-encoder rerank (bge-reranker-base, local in container)
      │      ──▶ Supabase pgvector (PostgreSQL + pgvector extension)
      │
      ├──▶ generate (src/policylens/generate.py)
      │      abstain if all scores < 0.30 (no LLM call)
      │      otherwise call Claude Haiku/Sonnet with clause-only context
      │      parse [N] citation markers → CitedChunk list
      │      ──▶ Claude API (via Anthropic SDK)
      │
      └──▶ trace (src/policylens/observability.py)
             retrieve / rerank / generate spans
             cost_usd, latency_ms, token counts
             ──▶ LangFuse (silent no-op if keys absent)
```

### What is measured

Every CI run on `main` (when `ANTHROPIC_API_KEY` is set):
- **Ragas faithfulness** — does the answer stay within the retrieved clauses?
  Gate: must be >= threshold in `eval/thresholds.yaml` (default 0.80;
  recalibrate from `eval/baselines/baseline_v1.json` after first run).
- **Abstention accuracy** — does the system abstain on unanswerable questions?
  Gate: >= 0.90 on the 30 unanswerable items in `golden_v1.jsonl`.
- **Answer relevancy, context precision/recall** — Ragas metrics reported but
  not gated in v1.
- **Citation recall/precision** — house metrics from `eval/metrics.py`.

Eval artifacts are uploaded to GitHub Actions as run artifacts and retained for
30 days.

### What it costs to run

**Demo (local Streamlit):** zero API costs for retrieval (local Chroma + local
embeddings). Generation: ~300–500 input tokens + ~200 output tokens per
question to `claude-haiku-4-5` ≈ **$0.001–0.002 per question**.

**Production API (Lambda):** Lambda is a 3 GB container (bge-reranker-base
requires 2+ GB), reserved concurrency 5. Cold start is ~5–10 seconds on first
request; subsequent requests warm (< 1 second for retrieval + rerank). Lambda
+ API Gateway costs are negligible at demo traffic. Anthropic costs are the
same per query as above.

**Eval suite (CI):** the Ragas judge uses `claude-opus-4-8` to evaluate 167
items. Approximate cost: **$2–5 per full CI eval run**. The eval job only runs
when `ANTHROPIC_API_KEY` is present as a repo secret — it is skipped on forks
and keyless PRs.

**Supabase:** the free tier (500 MB database, 2 CPU) is sufficient for 2,393
chunks with 384-dim embeddings. No egress costs for Lambda-to-Supabase traffic
within the same AWS region as your Supabase project.

**LangFuse:** the cloud free tier covers up to 50,000 observations/month.
Tracing is optional and a silent no-op when keys are absent.

### Security posture

**Authentication:** every `POST /ask` request must include an `x-api-key`
header matching the value in AWS Secrets Manager (`policylens/api_key`). The
authorizer is a separate Lambda (`api/authorizer.py`) using constant-time
comparison. It fails closed — any missing secret, missing header, or error
returns 403. Auth is enforced in Terraform on every deploy; there is no manual
console step that can be forgotten.

**Input validation:** the handler validates body size (< 8 KB), JSON shape,
field types, query length (1–500 chars), and `policy_id` against an explicit
allowlist before any embedding or LLM call. Malformed or unknown-policy
requests never touch Anthropic or Supabase.

**Throttling:** 5 req/s sustained, burst 10 (API Gateway stage throttle).
Lambda reserved concurrency: 5 (hard cap on concurrent containers). These
together bound throughput but do not cap daily spend — use the AWS Budget
hard-stop (SETUP_TASKS Group 0) as the authoritative dollar ceiling.

**Secrets:** credentials arrive via AWS Secrets Manager at cold start (not
environment variables baked into the image). The Lambda execution role reads
only the two specific secret ARNs provisioned by Terraform. The authorizer
role reads only the `api_key` secret.

**No per-key daily quota natively:** HTTP API v2 does not support native
usage-plan quotas (that requires REST API). For a hard daily cap per key,
attach an AWS WAF rate-based rule or migrate to REST API. The AWS Budget
hard-stop remains the recommended authoritative backstop.

---

## What worked

- **Clause-level chunking beats paragraph chunking.** Using OPP-115's
  human-curated data-practice categories as section boundaries produces
  semantically coherent chunks that retrieve well.
- **Two abstention paths.** A score-floor check before the LLM call prevents
  burning tokens on low-confidence retrieval. The LLM's own "UNANSWERABLE"
  signal catches cases where scores looked OK but the clauses still didn't
  support an answer.
- **Local embeddings.** `bge-small-en-v1.5` runs entirely on CPU, no embedding
  API key needed, and is fast enough for a demo.
- **Hybrid retrieval + reranking.** pgvector RRF (cosine + FTS) followed by
  `bge-reranker-base` improves precision on edge cases where keyword terms
  appear in low-scoring semantic chunks.
- **Auth enforced in code, not console.** The Lambda authorizer ensures the
  endpoint is never accidentally left open between deploys.

## What didn't / tradeoffs

- **PrivacyQA uses app policies; OPP-115 uses website policies.** The eval
  golden set queries are adapted from PrivacyQA but mapped onto OPP-115
  policies. The mapping adds noise — questions written for mobile-app policies
  don't always align with the website-policy corpus.
- **Citation extraction is regex-based.** Parsing `[N]` markers works well
  when the LLM follows instructions, but occasionally the model answers without
  citing. The fallback (cite the top hit) ensures the UI always shows
  something, but it's not always the right clause.
- **Lambda cold start with a 1.8 GB image is slow (~5–10 s).** Accepted for
  demo-scale traffic. Mitigations: provisioned concurrency (adds ~$15/month)
  or distilling the reranker to a smaller model.
- **Per-key daily quotas require WAF or REST API.** HTTP API v2 throttles by
  stage, not by caller. The Budget hard-stop is the financial backstop.

---

## Data sources & licenses

| Dataset | Source | License |
|---|---|---|
| OPP-115 | [usableprivacy.org](https://usableprivacy.org/data) — Wilson et al., ACL 2016 | Research/teaching only (CC-NC spirit). Cite the paper if you publish. |
| PrivacyQA | [GitHub: AbhilashaRavichander/PrivacyQA_EMNLP](https://github.com/AbhilashaRavichander/PrivacyQA_EMNLP) | MIT |

Raw data is git-ignored and never redistributed. The golden eval set
(`eval/golden/golden_v1.jsonl`) derives from PrivacyQA queries (MIT) mapped
onto OPP-115 policies — see `eval/golden/MANIFEST.md` for full provenance.
