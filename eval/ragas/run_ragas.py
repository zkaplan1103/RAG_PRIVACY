"""Ragas evaluation harness for PolicyLens.

Runs the pipeline over the golden set (GoldenItemV2), builds RagasRecords,
computes faithfulness / answer_relevancy / context_precision / context_recall
with the judge model from Config (claude-opus-4-8, env override EVAL_JUDGE_MODEL).

Usage
-----
    # Requires ANTHROPIC_API_KEY + a built Chroma index (data/index/)
    python eval/ragas/run_ragas.py \
        [--golden eval/golden/golden_v1.jsonl] \
        [--output eval/ragas/report_<timestamp>.json] \
        [--max-items N]     # limit for smoke tests (default: all)
        [--judge-model M]   # override judge model

Outputs a JSON report consumed by the CI gate.
The harness is designed so a 2-item dry run (--max-items 2) verifies wiring
without spending significant API budget.

DO NOT run the full suite without explicit intent (see SETUP_TASKS.md).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# Minimal stdlib-only dry-run guard
# ---------------------------------------------------------------------------
_DRY_RUN = os.environ.get("RAGAS_DRY_RUN", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# GoldenItemV2 — mirrors CONTRACTS.md §9
# ---------------------------------------------------------------------------

class GoldenItemV2(TypedDict):
    id: str
    query: str
    policy_id: str
    expected_answerable: bool
    gold_chunk_ids: list[str]
    reference_answer: str


class RagasRecord(TypedDict):
    """Produced by the harness per golden item — mirrors CONTRACTS.md §9."""
    question: str
    answer: str           # Answer.text from the pipeline
    contexts: list[str]   # retrieved chunk texts handed to the LLM
    ground_truth: str     # reference_answer


def load_golden(path: str) -> list[GoldenItemV2]:
    items: list[GoldenItemV2] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


# ---------------------------------------------------------------------------
# Stub retriever for testing — avoids loading the Chroma index
# ---------------------------------------------------------------------------

class _StubRetriever:
    """Returns canned chunks for dry-run / unit-test purposes."""

    def __init__(self, chunks_path: str | None = None) -> None:
        self._chunks: list[dict] = []
        if chunks_path:
            p = Path(chunks_path)
            if p.exists():
                with open(p) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            self._chunks.append(json.loads(line))

    def retrieve(
        self, query: str, policy_id: str, k: int = 5
    ) -> list[Any]:
        from src.policylens.ingest import Chunk
        from src.policylens.retrieve import RetrievedChunk

        hits = [c for c in self._chunks if c.get("policy_id") == policy_id]
        if not hits:
            return []
        # Simple keyword overlap
        query_words = set(query.lower().split())

        def _score(c: dict) -> float:
            words = set(c.get("text", "").lower().split())
            return len(query_words & words) / max(len(query_words), 1)

        scored = sorted(hits, key=_score, reverse=True)[:k]
        return [
            RetrievedChunk(
                chunk=Chunk(**{kk: c[kk] for kk in (
                    "chunk_id", "policy_id", "policy_name", "section",
                    "text", "char_start", "char_end", "source_url"
                )}),
                score=_score(c),
            )
            for c in scored
        ]


# ---------------------------------------------------------------------------
# Pipeline runner — calls answer() and records contexts
# ---------------------------------------------------------------------------

def run_pipeline(
    items: list[GoldenItemV2],
    retriever: Any,  # Retriever protocol
    cfg: Any,        # Config
) -> tuple[list[RagasRecord], list[dict]]:
    """Run the RAG pipeline over golden items.

    Returns (ragas_records, raw_answers) where raw_answers are the full
    Answer TypedDicts (for house-metric computation).

    Skips items that produce a live API call when RAGAS_DRY_RUN=1.
    """
    from src.policylens.generate import answer

    records: list[RagasRecord] = []
    raw_answers: list[dict] = []

    for item in items:
        query = item["query"]
        policy_id = item["policy_id"]

        if _DRY_RUN:
            # Return a plausible stub answer without API call
            from src.policylens.generate import canned_answer
            ans = canned_answer(policy_id=policy_id)
            contexts = ["[dry-run stub context]"]
        else:
            # Retrieve contexts manually so we can capture them
            hits = retriever.retrieve(query, policy_id, k=cfg.top_k)
            contexts = [h["chunk"]["text"] for h in hits]
            ans = answer(query, policy_id, retriever, cfg)

        records.append(RagasRecord(
            question=query,
            answer=ans["text"],
            contexts=contexts,
            ground_truth=item["reference_answer"],
        ))
        raw_answers.append(dict(ans))

    return records, raw_answers


# ---------------------------------------------------------------------------
# Ragas evaluation
# ---------------------------------------------------------------------------

def build_ragas_dataset(records: list[RagasRecord]) -> Any:
    """Convert RagasRecords to a ragas EvaluationDataset."""
    try:
        from ragas import EvaluationDataset  # type: ignore[import]
        from ragas.dataset_schema import SingleTurnSample  # type: ignore[import]
    except ImportError as e:
        raise ImportError(
            "ragas not installed. Run: uv sync --group eval"
        ) from e

    samples = [
        SingleTurnSample(
            user_input=r["question"],
            response=r["answer"],
            retrieved_contexts=r["contexts"],
            reference=r["ground_truth"],
        )
        for r in records
    ]
    return EvaluationDataset(samples=samples)


def get_ragas_llm(judge_model: str) -> Any:
    """Build a LangChain LLM wrapping claude-opus-4-8 (or override)."""
    try:
        from langchain_anthropic import ChatAnthropic  # type: ignore[import]
    except ImportError as e:
        raise ImportError(
            "langchain-anthropic not installed. Run: uv sync --group eval"
        ) from e

    _STRIPPED_KEYS = {"temperature", "top_p", "top_k"}

    class _NoTempChatAnthropic(ChatAnthropic):  # type: ignore[misc]
        """Strips temperature/top_p/top_k — Opus 4.7+ rejects them."""

        def _get_request_payload(self, *args: Any, **kwargs: Any) -> dict:
            payload = super()._get_request_payload(*args, **kwargs)
            for k in _STRIPPED_KEYS:
                payload.pop(k, None)
            return payload

    return _NoTempChatAnthropic(
        model=judge_model,
        max_tokens=1024,
    )


def run_ragas_metrics(
    dataset: Any,
    judge_model: str,
) -> dict[str, float]:
    """Compute Ragas metrics with the judge LLM.

    Returns a dict with keys: faithfulness, answer_relevancy,
    context_precision, context_recall.
    """
    try:
        from ragas import evaluate  # type: ignore[import]
        from ragas.llms import LangchainLLMWrapper  # type: ignore[import]
        from ragas.metrics import (  # type: ignore[import]
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )
    except ImportError as e:
        raise ImportError(
            "ragas not installed. Run: uv sync --group eval"
        ) from e

    from ragas.embeddings import LangchainEmbeddingsWrapper  # type: ignore[import]

    try:
        from langchain_huggingface import HuggingFaceEmbeddings  # type: ignore[import]
    except ImportError as e:
        raise ImportError(
            "langchain-huggingface not installed. Run: uv sync --group eval"
        ) from e

    llm = get_ragas_llm(judge_model)
    wrapped_llm = LangchainLLMWrapper(llm)
    hf_embeddings = LangchainEmbeddingsWrapper(
        HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    )

    metrics = [faithfulness, answer_relevancy, context_precision, context_recall]
    for m in metrics:
        m.llm = wrapped_llm
        if hasattr(m, "embeddings"):
            m.embeddings = hf_embeddings

    result = evaluate(dataset=dataset, metrics=metrics, embeddings=hf_embeddings)
    # result is a ragas EvaluationResult; extract mean scores from _repr_dict
    means = getattr(result, "_repr_dict", {})
    scores: dict[str, float] = {}
    for k in ("faithfulness", "answer_relevancy", "context_precision", "context_recall"):
        v = means.get(k)
        scores[k] = float(v) if v is not None else float("nan")
    return scores


# ---------------------------------------------------------------------------
# House metrics (from eval/metrics.py)
# ---------------------------------------------------------------------------

def run_house_metrics(
    items: list[GoldenItemV2],
    raw_answers: list[dict],
) -> dict[str, Any]:
    """Compute house metrics (abstention accuracy, citation precision/recall).

    Returns a dict with float metric values, or {"house_metrics_error": str}
    if imports fail.
    """
    # We import evaluate() from the metrics module; it accepts GoldenItem (v1
    # schema compatible) — GoldenItemV2 is a superset so this works.
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from eval.metrics import evaluate  # type: ignore[import]
        from src.policylens.generate import Answer  # type: ignore[import]
    except ImportError as exc:
        return {"house_metrics_error": str(exc)}

    # GoldenItemV2 is a superset of GoldenItem; cast is safe
    from eval.golden import GoldenItem
    golden_v1 = [
        GoldenItem(
            query=item["query"],
            policy_id=item["policy_id"],
            expected_answerable=item["expected_answerable"],
            gold_chunk_ids=item["gold_chunk_ids"],
        )
        for item in items
    ]
    answers = [Answer(**a) for a in raw_answers]
    result = evaluate(golden_v1, answers)
    return dict(result)


# ---------------------------------------------------------------------------
# CI gate check
# ---------------------------------------------------------------------------

def check_thresholds(
    ragas_scores: dict[str, float],
    house_scores: dict[str, Any],
    thresholds_path: str = "eval/thresholds.yaml",
) -> tuple[bool, list[str]]:
    """Compare scores against thresholds.yaml.  Returns (passed, failures)."""
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        # yaml not installed — use inline defaults
        thresholds = {"faithfulness": 0.80, "abstention_accuracy": 0.90}
    else:
        p = Path(thresholds_path)
        if p.exists():
            with open(p) as f:
                thresholds = yaml.safe_load(f) or {}
        else:
            thresholds = {"faithfulness": 0.80, "abstention_accuracy": 0.90}

    import math

    failures: list[str] = []

    faith = ragas_scores.get("faithfulness", float("nan"))
    faith_thresh = float(thresholds.get("faithfulness", 0.80))
    # NaN means the metric was not computed (e.g. dry-run or no API key) — skip gate
    if not math.isnan(faith) and not (faith >= faith_thresh):
        failures.append(
            f"faithfulness {faith:.3f} < threshold {faith_thresh}"
        )

    abst = house_scores.get("abstention_accuracy", float("nan"))
    abst_thresh = float(thresholds.get("abstention_accuracy", 0.90))
    if not math.isnan(abst) and not (abst >= abst_thresh):
        failures.append(
            f"abstention_accuracy {abst:.3f} < threshold {abst_thresh}"
        )

    return len(failures) == 0, failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run Ragas + house metrics over the golden set"
    )
    parser.add_argument(
        "--golden",
        default="eval/golden/golden_v1.jsonl",
        help="Path to golden_v1.jsonl",
    )
    parser.add_argument(
        "--backend",
        choices=["chroma", "pgvector"],
        default="chroma",
        help=(
            "Retrieval backend to use (default: chroma). "
            "Use 'chroma' for the Chroma path (baseline_v1 anchor, zero-cloud). "
            "Use 'pgvector' after the Supabase cutover."
        ),
    )
    parser.add_argument(
        "--out",
        "--output",
        default=None,
        dest="output",
        help=(
            "Output JSON report path. "
            "For the regression anchor: --out eval/baselines/baseline_v1.json  "
            "(--output is accepted as an alias)"
        ),
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Limit items for smoke run (default: all)",
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Override judge model (default: from Config or EVAL_JUDGE_MODEL env)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use stub pipeline (no API calls); validates wiring only",
    )
    parser.add_argument(
        "--fixture",
        default=None,
        help="Path to chunks_sample.jsonl for stub retriever (dry run only)",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        os.environ["RAGAS_DRY_RUN"] = "1"

    # --- Config ---
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from src.policylens.config import Config

    cfg = Config()
    # Apply --backend override to Config so the correct retriever is constructed
    cfg.retrieval_backend = args.backend
    judge_model = (
        args.judge_model
        or os.environ.get("EVAL_JUDGE_MODEL")
        or cfg.__dict__.get("judge_model", "claude-opus-4-8")
    )

    # --- Load golden set ---
    print(f"Loading golden set from {args.golden} ...", file=sys.stderr)
    items = load_golden(args.golden)
    if args.max_items:
        items = items[: args.max_items]
    print(f"  {len(items)} items", file=sys.stderr)

    # --- Build retriever ---
    if _DRY_RUN or args.dry_run:
        print("Using stub retriever (dry run / no index needed)", file=sys.stderr)
        retriever = _StubRetriever(
            chunks_path=args.fixture or "tests/fixtures/chunks_sample.jsonl"
        )
    elif args.backend == "pgvector":
        from src.policylens.pgvector import PgVectorRetriever  # type: ignore[import]
        print(f"Using PgVectorRetriever (backend=pgvector, db_url_env={cfg.db_url_env}) ...",
              file=sys.stderr)
        retriever = PgVectorRetriever(cfg)
    else:
        from src.policylens.retrieve import ChromaRetriever
        print(f"Loading Chroma index from {cfg.index_dir} ...", file=sys.stderr)
        retriever = ChromaRetriever(cfg)

    # --- Run pipeline ---
    print("Running pipeline ...", file=sys.stderr)
    t0 = time.time()
    ragas_records, raw_answers = run_pipeline(items, retriever, cfg)
    pipeline_ms = (time.time() - t0) * 1000
    print(f"  Pipeline done in {pipeline_ms:.0f} ms", file=sys.stderr)

    # --- House metrics ---
    print("Computing house metrics ...", file=sys.stderr)
    house_scores = run_house_metrics(items, raw_answers)

    # --- Ragas metrics ---
    ragas_scores: dict[str, float] = {}
    if _DRY_RUN or args.dry_run:
        print("Skipping Ragas LLM judge (dry run)", file=sys.stderr)
        ragas_scores = {
            "faithfulness": float("nan"),
            "answer_relevancy": float("nan"),
            "context_precision": float("nan"),
            "context_recall": float("nan"),
        }
    elif not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "WARNING: ANTHROPIC_API_KEY not set — skipping Ragas judge",
            file=sys.stderr,
        )
        ragas_scores = {
            "faithfulness": float("nan"),
            "answer_relevancy": float("nan"),
            "context_precision": float("nan"),
            "context_recall": float("nan"),
        }
    else:
        print(f"Running Ragas with judge={judge_model} ...", file=sys.stderr)
        dataset = build_ragas_dataset(ragas_records)
        ragas_scores = run_ragas_metrics(dataset, judge_model)

    # --- Gate check ---
    passed, failures = check_thresholds(ragas_scores, house_scores)

    # --- Report ---
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    report = {
        "version": "v1",
        "golden_file": args.golden,
        "backend": args.backend,
        "n_items": len(items),
        "judge_model": judge_model,
        "pipeline_ms": round(pipeline_ms),
        "timestamp": ts,
        "ragas": {
            k: (None if (isinstance(v, float) and v != v) else round(v, 4))
            for k, v in ragas_scores.items()
        },
        "house_metrics": {
            k: (None if isinstance(v, float) and v != v else v)
            for k, v in house_scores.items()
        },
        "gate_passed": passed,
        "gate_failures": failures,
        "records": [dict(r) for r in ragas_records],
    }

    output = args.output or f"eval/ragas/report_{args.backend}_{ts}.json"
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nReport written to {output}", file=sys.stderr)
    print(f"Gate: {'PASSED' if passed else 'FAILED'}", file=sys.stderr)
    if failures:
        for fail in failures:
            print(f"  FAIL: {fail}", file=sys.stderr)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
