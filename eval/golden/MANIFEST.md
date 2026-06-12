# Golden Eval Set — MANIFEST

## Version: v1

**File:** `eval/golden/golden_v1.jsonl`
**Schema:** `GoldenItemV2` (CONTRACTS.md §9)
**Built:** 2026-06-12
**Immutable once tagged.** Edits produce `golden_v2.jsonl`.

---

## Item Counts

| Category | Count | Percentage |
|---|---|---|
| Total | 167 | 100% |
| Answerable | 137 | 82.0% |
| Unanswerable | 30 | 18.0% |

Target: 150–200 items, ≥15% unanswerable. Both targets met.

---

## Provenance

### Source 1: Adapted PrivacyQA Queries (~30 items)

**License:** MIT (AbhilashaRavichander/PrivacyQA_EMNLP, GitHub)
**Citation:** Ravichander et al., "Question Answering for Privacy Policies: Combining Computational and Legal Perspectives", EMNLP 2019.
**URL:** https://github.com/AbhilashaRavichander/PrivacyQA_EMNLP

Queries were selected from `policy_test_data.csv` by reading the Query column and
picking semantically general questions that transfer from mobile-app privacy
policies (PrivacyQA domain) to website privacy policies (OPP-115 domain).
Query text is used verbatim or with minor grammatical normalization.

**Mapping strategy:** Each adapted query was manually assigned to the OPP-115
section most likely to contain a supporting answer (e.g., "do you sell my data"
→ `Third Party Sharing/Collection`). The gold chunk is the longest chunk in that
section for the target policy. Reference answers are extractive (first ≤300 chars
of the gold chunk text), not LLM-generated.

**Limitation:** Reference answers are extractive snippets, not fluent
human-written ground truth. A future curation pass with human annotators (or a
dedicated LLM judge pass, outside CI) would improve Ragas context_recall and
answer_relevancy scores. Flag for golden_v2.

### Source 2: Hand-curated Unanswerable Items (~30 items)

Questions about topics **genuinely absent** from OPP-115 website policies:
biometric data, GDPR Article 6 legal basis, blockchain, federated learning,
IoT devices, CCPA rights, deepfakes, voice recordings, etc.

These were designed to test the abstention path. `gold_chunk_ids=[]` and
`reference_answer=""` for all unanswerable items.

### Source 3: Hand-curated Answerable Items (~107 items)

Extractive Q&A pairs derived directly from OPP-115 chunk text. Coverage targets:
- Every OPP-115 section type (First Party Collection/Use, Third Party
  Sharing/Collection, Data Security, User Choice/Control, User Access/Edit/Deletion,
  Policy Change, Data Retention, Do Not Track, International and Specific Audiences)
- Spread across ≥25 different policies to avoid single-policy bias
- Reference answers are extractive (≤300 chars of gold chunk text)

---

## OPP-115 Source

**License:** Creative Commons Attribution Non-Commercial (CC-NC) — research and
teaching use only.
**Citation:** Wilson et al., "The Creation and Analysis of a Website Privacy Policy
Corpus", ACL 2016.
**Data:** 115 sanitized website privacy policies with annotation CSVs.
**Chunk corpus:** `data/processed/chunks.jsonl` (2393 chunks, 115 policies).

---

## Curation Rules

1. Each item must reference a `policy_id` that exists in `chunks.jsonl`.
2. Each answerable item must have at least one `gold_chunk_id` that exists in
   `chunks.jsonl` for that `policy_id`.
3. `reference_answer` must be non-empty for answerable items.
4. Unanswerable items: `gold_chunk_ids=[]`, `reference_answer=""`.
5. IDs (`gv1-XXXX`) are assigned by `build_golden.py` deterministically (sorted by
   `policy_id + query`) and are stable across reruns with the same seed (42).
6. The set is reproducible: run `python eval/golden/build_golden.py` to regenerate.

---

## Reproducibility

```bash
# Re-generate from source corpora (no API key needed):
python eval/golden/build_golden.py \
  --chunks /path/to/data/processed/chunks.jsonl \
  --output eval/golden/golden_v1.jsonl
```

The build script is deterministic (seed=42). PrivacyQA query texts are baked in
as Python constants (not loaded from CSV at build time), so the build works even
without the PrivacyQA files present.

---

## Known Limitations

- Reference answers are extractive snippets, not fluent human-written ground
  truth. Ragas `answer_relevancy` scores may be depressed vs. a fluent reference.
- PrivacyQA–OPP-115 corpus mismatch: PrivacyQA queries are about mobile apps;
  OPP-115 policies are website policies. Section-level mapping may not capture
  the exact passage a human annotator would cite.
- A planned golden_v2 should include human-verified reference answers and
  multi-chunk gold sets for complex questions.
