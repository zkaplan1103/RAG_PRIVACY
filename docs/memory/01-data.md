---
tags: [data]
---

# Data-engineer memory

## 2026-06-01 | orchestrator (Phase 0 handoff)

- OPP-115 source: `data/raw/opp115/` — 115 sanitized HTML policy files (`sanitized_policies/`) + 115 annotation CSVs (`annotations/`).
  Acquired via GitHub mirror (pmayostendorp/beforeiaccept); official URL requires session login.
  **License: CC-NC, research/teaching only. Cite Wilson et al. ACL 2016. Do NOT redistribute raw files.**
  Key dirs: `sanitized_policies/` (HTML, clean), `annotations/` (CSV per policy, practice categories).
- PrivacyQA source: `data/raw/privacyqa/` — cloned from GitHub AbhilashaRavichander/PrivacyQA_EMNLP.
  Contains app-policy Q&A with unanswerable questions (~1,750 Qs, ~3,500 annotations).
- PolicyQA: **large** (~25k rows) — sample only with `head`/`jq`, never load whole file.
  In `data/raw/policyqa/` if needed; not required for Phase 1 chunk building.
- Output: `data/processed/chunks.jsonl` — one JSON object per line, Chunk schema from docs/CONTRACTS.md §1.
- Chunk target: 150–400 tokens. Split on OPP-115 data-practice categories as natural section boundaries.
- `chunk_id` format: `{policy_id}::{section_slug}::c{index:03d}` — must be deterministic and unique.

## 2026-06-01 | data-engineer (Phase 1)
- 115 policies parsed, 2393 chunks written to `data/processed/chunks.jsonl`
- Section distribution: top categories include "Other", "First Party Collection/Use", "Third Party Sharing/Collection", "Data Security", "Policy Change", "User Choice/Control"
- Sanitized HTML format: segments separated by "|||"; annotations CSV has no header (cols: annotation_id, batch_id, annotator_id, policy_id, segment_id, category, attrs_json, date, url)
- Chunking: adjacent same-category segments merged; oversized chunks split at sentence boundaries (MAX_CHARS=1600 ≈ 400 tokens)
- Fixture updated: 10 real chunks from 4 policies (sci-news.com, redorbit.com, aol.com, honda.com), 9 distinct sections
- Regenerate: `uv run python -c "from src.policylens.ingest import ingest; from src.policylens.config import DEFAULT_CONFIG; ingest('data/raw/opp115', 'data/processed/chunks.jsonl', DEFAULT_CONFIG)"`
