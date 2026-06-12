# PolicyLens

> Ask plain-English questions about privacy policies. Every answer cites the exact clause it used — or tells you the policy doesn't address it.

Privacy policies are long, dense, and written by lawyers. PolicyLens lets you ask a normal question ("Does this app share my data with advertisers?") and get a short, honest answer that points directly to the clause that supports it — or says "the policy doesn't address this" when the answer isn't there.

---

## Demo

1. Pick a policy from the sidebar (Amazon, AOL, Honda, and others from the OPP-115 corpus)
2. Type a question or click an example
3. Get a plain-English answer with expandable citation cards showing the exact clause

If no relevant clause scores above the confidence threshold, the app says so — it never makes something up.

---

## How it works

PolicyLens is a **RAG** (Retrieval-Augmented Generation) system with three stages:

**1. Ingest** — 115 real website privacy policies from the [OPP-115 corpus](https://usableprivacy.org/data) are parsed into 2,393 clause-level chunks (~150–400 words each), labeled with their data-practice category (e.g. "Third Party Sharing", "Data Security").

**2. Retrieve** — When you ask a question, it's embedded using a local sentence-transformer model (`bge-small-en-v1.5`) and compared against every chunk for the selected policy using cosine similarity. The top-5 most relevant clauses are returned.

**3. Generate** — Those clauses — and only those clauses — are handed to Claude (Haiku). The prompt explicitly instructs the model not to use outside knowledge. It either answers with inline citations (`[1]`, `[2]`) or says "UNANSWERABLE" if the clauses don't support a response.

The citations you see in the UI are traceable to real chunk IDs and real text from the source document.

---

## Quickstart

**Requirements:** Python 3.11+, [`uv`](https://github.com/astral-sh/uv), an [Anthropic API key](https://console.anthropic.com)

```bash
# 1. Install dependencies
uv sync

# 2. Build the vector index (one-time, ~5 min on CPU)
make index

# 3. Run the app
export ANTHROPIC_API_KEY=sk-ant-...
uv run streamlit run app.py
```

The index is cached to `data/index/` — rebuilding is skipped on subsequent runs.

---

## Project layout

```
src/policylens/
  config.py     shared Config dataclass
  ingest.py     parse OPP-115 HTML → chunks.jsonl
  index.py      embed chunks → Chroma vector store
  retrieve.py   ChromaRetriever (production) + FixtureRetriever (tests)
  generate.py   answer() with citations and abstention
app.py          Streamlit demo
eval/
  golden.py     load_golden() — maps PrivacyQA onto GoldenItem schema
  metrics.py    evaluate() interface — metrics harness (Project C)
tests/          unit tests (32 passing); fixtures/chunks_sample.jsonl
data/raw/       OPP-115 + PrivacyQA corpora (git-ignored)
data/index/     Chroma vector store (git-ignored)
```

---

## Running tests

```bash
uv run pytest tests/ -v        # 32 unit tests, no API key needed
make smoke                     # 5-question stub smoke test
make smoke-real                # end-to-end with real LLM (requires API key)
```

---

## What worked

- **Clause-level chunking beats paragraph chunking.** Using OPP-115's human-curated data-practice categories as section boundaries produces semantically coherent chunks that retrieve well.
- **Two abstention paths.** A score-floor check before the LLM call prevents burning tokens on low-confidence retrieval. The LLM's own "UNANSWERABLE" signal catches cases where scores looked OK but the clauses still didn't support an answer.
- **Local embeddings.** `bge-small-en-v1.5` runs entirely on CPU, no embedding API key needed, and is fast enough for a demo.

## What didn't / tradeoffs

- **PrivacyQA uses app policies; OPP-115 uses website policies.** The eval golden set (PrivacyQA) can't be directly scored against the OPP-115 index without aligning the two corpora — that mapping is left for Project C.
- **Citation extraction is regex-based.** Parsing `[1]` markers works well when the LLM follows instructions, but occasionally Haiku answers without citing. The fallback (cite the top hit) ensures the UI always shows something, but it's not always the right clause.
- **No re-ranking.** A cross-encoder re-ranker would sharpen precision at the cost of latency. Skipped to keep the stack simple.

## Cost per query

~300–500 input tokens + ~200 output tokens to `claude-haiku-4-5`.  
Approximately **$0.001–0.002 per question** at current Haiku pricing.

---

## Production upgrade (in progress)

The demo above works today. The project is currently being upgraded into a
production-grade, *measured* system — plan in [docs/UPGRADE_PLAN.md](docs/UPGRADE_PLAN.md),
interfaces in [docs/CONTRACTS.md](docs/CONTRACTS.md) (Part II):

- **Evaluation:** a versioned golden set (150–200 Q/A derived from PrivacyQA), scored with Ragas (faithfulness, answer relevance, context precision/recall) and promptfoo, run in CI with a regression gate that fails the build if faithfulness drops below threshold.
- **Observability:** LangFuse tracing of every answer — retrieval, reranking, and generation spans with cost and latency.
- **Retrieval:** Chroma → pgvector on Supabase, with hybrid search (vector + full-text, RRF fusion) and a local cross-encoder reranker — behind the same `Retriever` interface, so abstention and citations are unchanged.
- **Deployment:** AWS Lambda (container) + API Gateway, provisioned via Terraform.

Until that lands, everything in this README describes the current, working demo.

---

## Data sources & licenses

| Dataset | Source | License |
|---|---|---|
| OPP-115 | [usableprivacy.org](https://usableprivacy.org/data) — Wilson et al., ACL 2016 | Research/teaching only (CC-NC spirit). Cite the paper if you publish. |
| PrivacyQA | [GitHub: AbhilashaRavichander/PrivacyQA_EMNLP](https://github.com/AbhilashaRavichander/PrivacyQA_EMNLP) | MIT |

Raw data is git-ignored and never redistributed.
