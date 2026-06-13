# TESTING_CHECKLIST.md — PolicyLens end-to-end verification

Run these checks in order after completing `SETUP_TASKS.md`. Each item has an
exact command and the expected output. A check fails if the output does not
match. Do not move to a later check until the earlier one passes.

---

## 1. Local keyless test suite

No API key or cloud account needed. Verifies the core library, handler, gate,
and observability no-op path.

```bash
uv run pytest tests/ -v --tb=short
```

**Expected:** all tests pass; one known skip (`test_abstain_model_says_unanswerable`
is a pre-existing flake — acceptable). The fast suite completes in under 2
minutes; the slow retrieval suite (`tests/test_retrieval.py`) takes ~30 minutes
on CPU without the HuggingFace model cache.

**Narrowed fast run (skips slow retrieval tests):**
```bash
uv run pytest tests/ --ignore=tests/test_retrieval.py -v --tb=short
```
Expected: 156+ tests pass, 0 failures.

**Lint and type checks:**
```bash
uv run ruff check .
uv run pyright
```
Expected: 0 errors from both.

---

## 2. pgvector connection and parity spot-check

**Prerequisite:** `SUPABASE_DB_URL` set; backfill complete (SETUP_TASKS Step 4.2).

### 2a. pgvector connection

```bash
SUPABASE_DB_URL="..." uv run python -c "
from src.policylens.config import Config
from src.policylens.pgvector import PgVectorRetriever
cfg = Config(retrieval_backend='pgvector')
r = PgVectorRetriever(cfg)
hits = r.retrieve('data collection', '105_amazon_com', k=3)
print('hits:', [h['chunk']['chunk_id'] for h in hits])
r.close()
"
```
Expected: a list of 3 chunk IDs; no exceptions.

### 2b. Parity spot-check (same query, both backends)

Run the same question through Chroma and pgvector and compare the top chunk IDs.
They do not need to be identical, but they should overlap significantly (expect
2–3 of the top-3 chunks to match between backends).

```bash
uv run python -c "
from src.policylens.config import Config
from src.policylens.retrieve import make_retriever

query = 'What data does this policy collect?'
policy_id = '105_amazon_com'

# Chroma
cfg_c = Config(retrieval_backend='chroma')
r_c = make_retriever(cfg_c)
chroma_hits = [h['chunk']['chunk_id'] for h in r_c.retrieve(query, policy_id, k=3)]
print('Chroma  top-3:', chroma_hits)

# pgvector (requires SUPABASE_DB_URL)
import os
if os.environ.get('SUPABASE_DB_URL'):
    cfg_p = Config(retrieval_backend='pgvector')
    r_p = make_retriever(cfg_p)
    pg_hits = [h['chunk']['chunk_id'] for h in r_p.retrieve(query, policy_id, k=3)]
    print('pgvector top-3:', pg_hits)
    overlap = set(chroma_hits) & set(pg_hits)
    print('overlap:', len(overlap), '/ 3')
    r_p.close()
else:
    print('SUPABASE_DB_URL not set — pgvector parity check skipped')
"
```
Expected: overlap of 2 or 3 out of 3 chunks.

---

## 3. Abstention still works through the full pipeline

**Prerequisite:** `ANTHROPIC_API_KEY` set.

Ask a question that the OPP-115 policies do not address. The system must
abstain rather than hallucinate.

```bash
ANTHROPIC_API_KEY=... uv run python -c "
from src.policylens.config import DEFAULT_CONFIG
from src.policylens.retrieve import make_retriever
from src.policylens.generate import answer

retriever = make_retriever(DEFAULT_CONFIG)
result = answer(
    'What is the company chief executive officer name?',
    '105_amazon_com',
    retriever,
    DEFAULT_CONFIG,
)
print('answerable:', result['answerable'])
print('text:', result['text'][:120])
print('citations:', result['citations'])
"
```
Expected: `answerable: False`, `citations: []`, `text` contains the abstention
message ("policy doesn't address" or similar). The system must NOT fabricate
a CEO name.

---

## 4. API key authentication (auth gates)

**Prerequisite:** deploy complete (SETUP_TASKS Group 5). API endpoint and API
key from Step 5.5 and 5.7.

Set your variables:
```bash
API_ENDPOINT=$(terraform -chdir=infra output -raw api_endpoint)
API_KEY="<your-api-key-from-step-5.7>"
```

### 4a. No key → 403 (authorizer blocks before handler runs)

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$API_ENDPOINT/ask" \
  -H "Content-Type: application/json" \
  -d '{"query":"test","policy_id":"105_amazon_com"}'
```
Expected: `403`

### 4b. Wrong key → 403

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$API_ENDPOINT/ask" \
  -H "Content-Type: application/json" \
  -H "x-api-key: wrong-key-value" \
  -d '{"query":"test","policy_id":"105_amazon_com"}'
```
Expected: `403`

### 4c. Correct key → handler runs (200 or 500 depending on secrets state)

```bash
curl -s -w '\nHTTP %{http_code}\n' -X POST "$API_ENDPOINT/ask" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{"query":"What data does this policy collect?","policy_id":"105_amazon_com"}'
```
Expected: HTTP 200 with a JSON body containing `answer`, `request_id`,
`latency_ms`, `version`. The `answer.answerable` field should be `true`.

---

## 5. Input validation (400 / 404 responses)

**Prerequisite:** API endpoint live with correct `x-api-key`.

### 5a. Missing body → 400

```bash
curl -s -w '\nHTTP %{http_code}\n' -X POST "$API_ENDPOINT/ask" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY"
```
Expected: HTTP 400, body contains `"error": "Request body is required"`.

### 5b. Missing required field → 400

```bash
curl -s -w '\nHTTP %{http_code}\n' -X POST "$API_ENDPOINT/ask" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{"policy_id":"105_amazon_com"}'
```
Expected: HTTP 400, body contains `"error"` mentioning `query`.

### 5c. Query too long → 400

```bash
LONG_QUERY=$(python3 -c "print('x' * 501)")
curl -s -w '\nHTTP %{http_code}\n' -X POST "$API_ENDPOINT/ask" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{\"query\":\"$LONG_QUERY\",\"policy_id\":\"105_amazon_com\"}"
```
Expected: HTTP 400, body contains `"error"` mentioning `query` exceeds max
length of 500 characters.

### 5d. Unknown policy_id → 404

```bash
curl -s -w '\nHTTP %{http_code}\n' -X POST "$API_ENDPOINT/ask" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{"query":"test","policy_id":"not_a_real_policy"}'
```
Expected: HTTP 404, body contains `"error"` mentioning `Unknown policy_id`.

**Important:** the 404 is returned BEFORE any embedding or LLM call. No
Anthropic API cost is incurred on this request.

### 5e. Invalid JSON → 400

```bash
curl -s -w '\nHTTP %{http_code}\n' -X POST "$API_ENDPOINT/ask" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d 'not valid json'
```
Expected: HTTP 400, body contains `"error"` mentioning valid JSON.

---

## 6. LangFuse trace inspection

**Prerequisite:** `LANGFUSE_*` env vars set (SETUP_TASKS Step 2.3). At least
one real request made through the API (Check 4c).

1. Open https://cloud.langfuse.com (or your self-hosted instance).
2. Navigate to your "policylens" project → Traces.
3. Find a trace named `"answer"` from the last few minutes.
4. Verify it contains three spans:
   - `retrieve` — should show `backend: pgvector` (or `chroma`), `top_k: 5`,
     `candidate_count > 0`, and `latency_ms`.
   - `rerank` — present only on the pgvector path with `rerank_enabled=True`.
     Shows `model: BAAI/bge-reranker-base`, `candidates_in`, `candidates_out`,
     `score_deltas`.
   - `generate` — shows `model: claude-haiku-4-5` (or sonnet), `input_tokens`,
     `output_tokens`, `cost_usd`, `abstention_path`.
5. On the trace root, verify metadata: `policy_id`, `answerable`,
   `n_citations`, `latency_ms` are all present.

**No-op check (optional):** run the pipeline locally with `LANGFUSE_*` vars
unset and confirm no network errors appear and the answer is unchanged:
```bash
env -i ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY uv run python -c "
from src.policylens.config import DEFAULT_CONFIG
from src.policylens.retrieve import make_retriever
from src.policylens.generate import answer
r = make_retriever(DEFAULT_CONFIG)
a = answer('Does Amazon share data?', '105_amazon_com', r, DEFAULT_CONFIG)
print('answerable:', a['answerable'], '| no LangFuse errors above')
"
```
Expected: answer returned normally, zero LangFuse-related errors.

---

## 7. Full end-to-end curl against the deployed endpoint

**Prerequisite:** Checks 4 and 5 pass.

```bash
curl -s -X POST "$API_ENDPOINT/ask" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{"query": "What data does this policy collect?", "policy_id": "105_amazon_com"}' \
  | python3 -m json.tool
```

Expected response shape:
```json
{
  "answer": {
    "answerable": true,
    "text": "...",
    "citations": [
      {"chunk_id": "105_amazon_com::...", "section": "...", "quote": "..."}
    ],
    "policy_id": "105_amazon_com",
    "model": "claude-haiku-4-5"
  },
  "request_id": "<uuid>",
  "latency_ms": <number>,
  "version": "<git-sha>"
}
```

Verify:
- `answer.answerable` is `true`
- `answer.citations` is non-empty (at least one entry)
- `answer.citations[0].chunk_id` starts with `105_amazon_com::`
- `request_id` is a UUID
- `latency_ms` is a positive number
- `version` matches the git SHA from the Docker build

---

## 8. CI green run

**Prerequisite:** GitHub secrets set (SETUP_TASKS Group 6).

```bash
git commit --allow-empty -m "ci: trigger green-run check"
git push
```

Then watch GitHub → Actions:

1. **checks** job (Lint / Type / Test): should complete green in ~5–10 min.
   - Ruff: 0 errors
   - Pyright: 0 errors
   - Fast tests: all pass
   - Slow retrieval tests: all pass (uses HF model cache on second run)
   - Smoke: passes

2. **eval** job (Eval / Faithfulness Gate): should complete green in ~30–60 min.
   - Ragas harness runs against `eval/golden/golden_v1.jsonl`
   - Gate step prints `faithfulness X.XX >= Y.YY PASS` and `abstention_accuracy`
   - Report artifact uploaded (visible in Actions → run → Artifacts)

---

## 9. Deliberately-broken gate test (prove the gate fails the build)

This test confirms the regression gate actually blocks a bad build. Run it on a
branch, not on `main`.

```bash
git checkout -b test/broken-gate
```

Edit `eval/thresholds.yaml` to set an impossibly high threshold:
```yaml
faithfulness: 0.9999
abstention_accuracy: 0.90
```

```bash
git add eval/thresholds.yaml
git commit -m "test: artificially high faithfulness threshold (should fail gate)"
git push origin test/broken-gate
```

Watch GitHub → Actions → eval job. The "Faithfulness + abstention gate" step
should print:
```
GATE FAILED:
  FAIL: faithfulness 0.74 < threshold 0.9999
```
And the job should show a red X (exit code 1). The `checks` job should still
pass (it does not run the gate).

After confirming the gate fails, revert:
```bash
git checkout main
git branch -D test/broken-gate
# Restore eval/thresholds.yaml to the calibrated values from Group 7
```

---

## Verification summary

| Check | Status |
|---|---|
| 1. Keyless test suite — all tests pass | |
| 2a. pgvector connection | |
| 2b. Chroma/pgvector parity (2+ of 3 chunks overlap) | |
| 3. Abstention works end-to-end | |
| 4a. No key → 403 | |
| 4b. Wrong key → 403 | |
| 4c. Correct key → 200 | |
| 5a. Missing body → 400 | |
| 5b. Missing field → 400 | |
| 5c. Query too long → 400 | |
| 5d. Unknown policy_id → 404 | |
| 5e. Invalid JSON → 400 | |
| 6. LangFuse trace has 3 spans (retrieve/rerank/generate) | |
| 7. End-to-end curl — 200, citations non-empty | |
| 8. CI checks job green | |
| 8. CI eval job green, gate passes | |
| 9. Broken gate correctly fails the build | |
