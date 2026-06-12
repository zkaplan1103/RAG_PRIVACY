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

## 2026-06-12 | eval-engineer (P2 complete — worktree-agent-a652a67dbbf036758)

Golden set v1 delivered: 167 items (137 answerable / 30 unanswerable = 18%), GoldenItemV2 schema.
Three provenance streams: ~30 adapted PrivacyQA queries (MIT license, queries-only, baked as constants),
30 hand-curated unanswerables (topics absent from OPP-115), ~107 hand-curated extractive Q&A pairs.
Reference answers are extractive snippets from gold chunk text (no LLM at build time, reproducible via build_golden.py).

eval/ragas/run_ragas.py now accepts --backend {chroma,pgvector} and --out PATH as required by CONTRACTS §9 /
UPGRADE_PLAN decision 8. Baseline command: `uv run python eval/ragas/run_ragas.py --backend chroma --out
eval/baselines/baseline_v1.json` (requires ANTHROPIC_API_KEY + built Chroma index; flag in SETUP_TASKS.md).

eval/metrics.py evaluate() implemented (abstention accuracy, answerable accuracy, citation recall/precision).
eval/thresholds.yaml: faithfulness=0.80, abstention_accuracy=0.90 (recalibrate after baseline_v1 run).
eval/baselines/README.md documents immutability rule and baseline_v1 creation command.
53 unit tests pass (ruff 0 errors on eval/ + test_eval.py, pyright 0 errors). Config v2 fields backported
to worktree. eval/golden/__init__.py exports GoldenItem so eval/metrics.py import resolves correctly.

Open items for golden_v2: fluent human-written reference answers; multi-chunk gold sets for complex questions.
