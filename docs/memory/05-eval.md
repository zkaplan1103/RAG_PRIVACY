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

## 2026-06-13 | eval-engineer (red-team citation-integrity fix)

Two bugs fixed in src/policylens/generate.py:

Finding #2 (Critical — _build_citations fabrication): The fallback at lines 132-139
that cited hits[0] when no valid [N] markers were present has been removed.
_build_citations now returns [] when no in-range markers are found.
_answer_impl now detects this and returns a standard abstention Answer
(answerable=False, ABSTENTION_TEXT, citations=[], abstention_path="no_valid_citation"
wired to observability) instead of fabricating a grounded-looking citation.

Finding #3 (Low — UNANSWERABLE startswith bug): Changed
`raw.upper().startswith("UNANSWERABLE")` to `raw.strip().upper() == "UNANSWERABLE"`
so that legitimate answers beginning "Unanswerable? No — ..." are no longer
wrongly treated as abstentions.

Tests: 11 tests in tests/red/test_red_citations.py (new regression guards);
5 new tests added to tests/test_generate.py. All 180 passing tests continue to
pass; total fast suite is now 181 collected (180 passed + 1 skipped).
ruff 0 errors, pyright 0 errors on generate.py.

GOLDEN-SET GATE IMPACT:
- abstention_accuracy (expected-unanswerable items, gate 0.90): EXPECTED IMPROVEMENT.
  Previously the system would fabricate a citation and return answerable=True on
  items where the model happened to omit [N] markers; those are now correctly
  abstained. Unanswerable items never should have produced citations, so any
  that did were false negatives for the abstention gate.
- answerable_accuracy / recall (expected-ANSWERABLE items): SMALL RECALL RISK.
  If a well-tuned model sometimes answers correctly but omits [N] markers, those
  items will now appear as incorrect abstentions (false abstentions). In practice
  claude-haiku-4-5 and claude-sonnet-4-6 are strong marker-followers on the OPP-115
  domain prompt; however this risk is real and should be measured on the next
  baseline run.
- faithfulness gate (0.80): NOT DIRECTLY AFFECTED.  Ragas faithfulness measures
  whether the answer text is supported by the retrieved context — items that now
  abstain correctly don't contribute a faithfulness score; removing them from the
  mean could move faithfulness up or down marginally.
- Recommendation: any baseline_v1.json captured before this commit is no longer
  comparable. Re-run the baseline (see eval/baselines/README.md) after deploying
  this fix to establish the new anchor. The note in eval/thresholds.yaml documents
  this requirement inline.
- Full Ragas run not executed (requires ANTHROPIC_API_KEY + real spend). Flagged
  for SETUP_TASKS.md.
