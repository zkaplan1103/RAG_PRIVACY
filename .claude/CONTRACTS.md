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
