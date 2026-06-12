# CONTRACTS.md — frozen interfaces

These are the seams between workstreams. **Freeze these in Phase 0.** Every
builder agent codes against the types/signatures here plus a small stub, which
is what lets the four agents run in parallel. Changing a contract = update this
file first, then log it in `docs/memory/00-decisions.md`, then notify affected agents.

Signatures are illustrative Python; keep names exact.

---

## 1. Chunk schema  (produced by `data-engineer`, consumed by `index-engineer`)

One JSON object per line in `data/processed/chunks.jsonl`:

```python
class Chunk(TypedDict):
    chunk_id: str        # stable, e.g. "google_pp::sec3::c07"
    policy_id: str       # e.g. "google_privacy_policy"
    policy_name: str     # human label, e.g. "Google Privacy Policy"
    section: str         # heading/category this chunk falls under
    text: str            # the clause text (clean, no HTML)
    char_start: int      # offset into the source doc (for exact-clause citation)
    char_end: int
    source_url: str | None
```

Rules: chunks are clause/section-aware (don't split mid-sentence); `chunk_id`
is deterministic and unique; `text` is plain text. Target ~150–400 tokens/chunk.

---

## 2. Retriever interface  (produced by `index-engineer`, consumed by `rag-engineer`)

```python
class RetrievedChunk(TypedDict):
    chunk: Chunk
    score: float         # similarity, higher = better

class Retriever(Protocol):
    def retrieve(self, query: str, policy_id: str, k: int = 5) -> list[RetrievedChunk]: ...
```

Rules: retrieval is scoped to a single `policy_id` (you ask questions about one
policy at a time). Returns at most `k`, sorted by score desc. Must work against
the 10-row fixture in `tests/fixtures/chunks_sample.jsonl` for isolated dev.

---

## 3. Answer schema  (produced by `rag-engineer`, consumed by `ui-engineer`)

```python
class Citation(TypedDict):
    chunk_id: str
    section: str
    quote: str           # SHORT supporting snippet (<= 25 words) for display

class Answer(TypedDict):
    answerable: bool         # False => abstain; ui shows "policy doesn't address this"
    text: str                # plain-English answer, or the abstention message
    citations: list[Citation]  # empty iff answerable is False
    policy_id: str
    model: str               # which LLM produced it (for the eval/report)

def answer(query: str, policy_id: str, retriever: Retriever, cfg: Config) -> Answer: ...
```

Rules: if `answerable` is False, `citations` is empty and `text` is the standard
abstention line. Every sentence of a real answer must trace to a returned chunk.
`quote` is a short snippet for UI highlighting, never a long copy of the clause.

---

## 4. Config  (shared; defined by orchestrator in Phase 0)

```python
@dataclass
class Config:
    embed_backend: str = "local"        # "local" | "openai"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    gen_backend: str = "anthropic"      # "anthropic" | "openai"
    gen_model: str = "claude-haiku-4-5"   # swap to a sonnet model for the final cut
    top_k: int = 5
    index_dir: str = "data/index"
    score_floor: float = 0.30           # below this for all hits => abstain
```

---

## 5. Eval seam  (Phase 3 stub; Project C implements)

```python
class GoldenItem(TypedDict):
    query: str
    policy_id: str
    expected_answerable: bool
    gold_chunk_ids: list[str]   # acceptable supporting chunks

def load_golden(path: str) -> list[GoldenItem]: ...   # maps PrivacyQA/PolicyQA onto our schema
# eval/ package exists with this signature documented; metrics NOT implemented here.
```

---
---

# Part II — Production upgrade contracts (v2, frozen 2026-06-11)

Additive only: nothing in §1–§5 changes. `Retriever`, `Chunk`, `Citation`, and
`Answer` are load-bearing for the upgrade and stay frozen. Change protocol is
unchanged: edit here first, log in `00-decisions.md`, then code.

## 6. Config v2  (extends §4 — new fields, backward-compatible defaults)

```python
@dataclass
class Config:
    # --- v1 fields unchanged (embed_*, gen_*, top_k, index_dir, score_floor) ---
    retrieval_backend: str = "chroma"     # "chroma" | "pgvector"
    db_url_env: str = "SUPABASE_DB_URL"   # env var NAME holding the Postgres DSN (never the DSN itself)
    hybrid_rrf_k: int = 60                # RRF constant for fusing vector + FTS ranks
    fts_candidates: int = 20              # candidates pulled from each leg before fusion
    rerank_enabled: bool = True           # pgvector path only; chroma path ignores
    rerank_model: str = "BAAI/bge-reranker-base"   # local cross-encoder
    rerank_top_n: int = 5                 # final k after rerank (== top_k by default)
    judge_model: str = "claude-opus-4-8"  # Ragas judge; env override EVAL_JUDGE_MODEL
    langfuse_enabled: bool = True         # auto-disables if LANGFUSE_* env vars absent
```

Rule: every default must keep the v1 demo working with zero new env vars set.

## 7. pgvector schema + hybrid retrieval  (vector-engineer)

One table, owned by the migration script (`src/policylens/pgvector.py` + `infra/sql/001_init.sql`):

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE chunks (
    chunk_id    text PRIMARY KEY,
    policy_id   text NOT NULL,
    policy_name text NOT NULL,
    section     text NOT NULL,
    text        text NOT NULL,
    char_start  int  NOT NULL,
    char_end    int  NOT NULL,
    source_url  text,
    embedding   vector(384) NOT NULL,             -- bge-small-en-v1.5
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);
CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_idx       ON chunks USING gin  (tsv);
CREATE INDEX chunks_policy_idx    ON chunks (policy_id);
```

```python
class PgVectorRetriever:  # implements the frozen Retriever protocol (§2) exactly
    def retrieve(self, query: str, policy_id: str, k: int = 5) -> list[RetrievedChunk]: ...
```

Rules: both legs (cosine ANN + FTS) are scoped to `policy_id`; fused via RRF
(`1/(rrf_k + rank)`); if `rerank_enabled`, cross-encoder rescores the fused
candidates and the **reranker score becomes `RetrievedChunk.score`, rescaled to
[0, 1]** so the §3 `score_floor` abstention semantics keep working. Sorted desc,
at most `k`. Backfill script reads `chunks.jsonl` (§1), reuses cached
embeddings where possible.

## 8. Observability contract  (observability-engineer)

`src/policylens/observability.py`. One **trace per `answer()` call**, three spans:

| Span | Captures |
|---|---|
| `retrieve` | backend, k, candidate count, top scores, policy_id |
| `rerank`   | model, in/out counts, score deltas (skipped span if disabled) |
| `generate` | model, input/output tokens, cost (from usage), abstention path taken |

Trace metadata: `policy_id`, `gen_model`, `score_floor`, `top_k`, `answerable`,
`n_citations`, `latency_ms`, `app_version` (git sha if available).

Rules: reads `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`;
if any is absent → **silent no-op** (zero network calls, zero log spam, demo
unaffected). Never traces raw policy text beyond the chunks already in the
prompt. `generate.py` and `api/handler.py` call the wrapper; they never import
the langfuse SDK directly.

## 9. Eval v2  (eval-engineer) — golden set, Ragas, promptfoo, gate

**Golden set:** `eval/golden/golden_v1.jsonl` + `eval/golden/MANIFEST.md`
(version, item count, provenance per source, license notes, curation rules).
Immutable once tagged — edits produce `golden_v2.jsonl`. Target 150–200 items,
including ≥ 15% expected-unanswerable.

```python
class GoldenItemV2(TypedDict):      # supersedes §5 GoldenItem for the v1 set file
    id: str                          # "gv1-0001" — stable across versions
    query: str
    policy_id: str                   # must exist in the OPP-115 index
    expected_answerable: bool
    gold_chunk_ids: list[str]        # may be empty only when expected_answerable is False
    reference_answer: str            # ground truth for Ragas ("" if unanswerable)

class RagasRecord(TypedDict):        # produced by the harness per golden item
    question: str
    answer: str                      # Answer.text from the pipeline
    contexts: list[str]              # retrieved chunk texts handed to the LLM
    ground_truth: str                # reference_answer
```

**Metrics:** Ragas `faithfulness`, `answer_relevancy`, `context_precision`,
`context_recall`, judged by `Config.judge_model`. Plus the §5 house metrics
(abstention accuracy, citation precision/recall) implemented in `eval/metrics.py`
(replaces the NotImplementedError stub — same `evaluate()` signature).

**promptfoo:** `eval/promptfoo/promptfooconfig.yaml` — provider wraps our
`answer()` via a python script provider; assertions mirror abstention +
citation rules on a fixed subset.

**Regression gate (CI):** mean faithfulness ≥ `FAITHFULNESS_THRESHOLD`
(default **0.80**, set in `eval/thresholds.yaml`, recalibrated after the first
baseline run) → otherwise the workflow fails. Abstention accuracy on
expected-unanswerable items ≥ 0.90 is a second hard gate.

## 10. API contract  (infra-engineer)

`POST /ask` (API Gateway → Lambda, `api/handler.py`):

```jsonc
// request
{ "query": "string (1–500 chars)", "policy_id": "string", "top_k": 5 }   // top_k optional
// 200 response — Answer (§3) plus envelope
{ "answer": { /* Answer schema, unchanged */ },
  "request_id": "uuid", "latency_ms": 1234, "version": "git-sha" }
// 400 invalid body · 404 unknown policy_id · 500 { "error": "...", "request_id": "..." }
```

Rules: handler is a thin adapter — all logic stays in `policylens.*`; the same
`answer()` serves Streamlit and Lambda. Response `answer` validates against §3
(structured output guarantees preserved). No streaming in v2.

## 11. Env var registry (single source of truth — SETUP_TASKS.md documents each)

| Var | Used by | Required for |
|---|---|---|
| `ANTHROPIC_API_KEY` | generate, eval | generation + Ragas judge |
| `SUPABASE_DB_URL` | PgVectorRetriever, migration | pgvector path only |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | observability | tracing only (absent → no-op) |
| `EVAL_JUDGE_MODEL` | eval | optional override of `Config.judge_model` |
| `FAITHFULNESS_THRESHOLD` | CI gate | optional override of thresholds.yaml |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | terraform, image push | deploy only (user-run) |

GitHub Actions mirrors these as repo secrets with identical names. Code never
hardcodes a credential or a DSN; Terraform takes them as variables, never state.
