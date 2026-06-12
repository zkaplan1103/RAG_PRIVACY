# eval/baselines/ — Immutable Regression Anchors

This directory holds baseline eval reports that serve as **immutable regression
anchors** for the CI faithfulness gate.

## Immutability rule

Once a baseline file is written here, **do not edit it**. It is the ground truth
that future eval runs are compared against. If a re-baselining is needed (e.g.,
after a deliberate quality improvement), create a new file (`baseline_v2.json`)
and update `eval/thresholds.yaml` with the recalibrated thresholds.

## baseline_v1.json (to be created by the user)

**UPGRADE_PLAN decision 8:** Before the pgvector retriever becomes the default,
run the full eval suite against the current **Chroma** retriever and save the
results as `eval/baselines/baseline_v1.json`. This is the regression anchor — all
future CI runs measure against it.

`baseline_v1.json` does not exist yet. It must be created by running the command
below (requires `ANTHROPIC_API_KEY` and a built Chroma index).

### Command (run this once, before the pgvector cutover)

```bash
# 1. Build the Chroma index if not already built
uv run python -m policylens.index build

# 2. Run the full eval suite on Chroma — saves the baseline
uv run python eval/ragas/run_ragas.py \
    --backend chroma \
    --out eval/baselines/baseline_v1.json \
    --golden eval/golden/golden_v1.jsonl

# 3. After this succeeds, commit the baseline:
#    git add eval/baselines/baseline_v1.json
#    git commit -m "eval: add Chroma baseline_v1 (UPGRADE_PLAN decision 8)"
```

### What the command produces

`baseline_v1.json` contains:
- Ragas metrics: `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`
- House metrics: `abstention_accuracy`, `answerable_accuracy`, `citation_recall`, `citation_precision`
- Per-item records (167 items from `golden_v1.jsonl`)

After running, update `eval/thresholds.yaml` with the observed baseline values
(recalibrate from the actual `faithfulness` score rather than using the default 0.80).

## File inventory

| File | Status | Notes |
|---|---|---|
| `baseline_v1.json` | **pending user action** | Full eval on Chroma, needs `ANTHROPIC_API_KEY` |
| `README.md` | committed | This file |
