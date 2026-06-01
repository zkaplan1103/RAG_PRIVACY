---
tags: [eval]
---

# Eval memory — Project C fills this in

## 2026-06-01 | orchestrator (Phase 0 handoff)

- Eval seam defined in docs/CONTRACTS.md §5. The `eval/` package stub is created in Phase 3.
- GoldenItem schema: query, policy_id, expected_answerable, gold_chunk_ids.
- load_golden(path) maps PrivacyQA/PolicyQA onto the Answer schema.
- Metrics (precision@k, recall@k, abstention accuracy) are NOT implemented here — Project C.
- PrivacyQA source for golden set: data/raw/privacyqa/. Includes expert answer annotations.
- PolicyQA: large (~25k rows) — sample only; do NOT load whole file into context.
