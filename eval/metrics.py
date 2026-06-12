"""Eval metrics — implements evaluate() replacing the NotImplementedError stub.

Interface contract (CONTRACTS.md §5, honored exactly):
  evaluate(golden: list[GoldenItem], answers: list[Answer]) -> EvalResult

Metrics:
  - abstention_accuracy: fraction of unanswerable items where the pipeline
    correctly returned answerable=False
  - answerable_accuracy: fraction of answerable items where the pipeline
    returned answerable=True (did not falsely abstain)
  - citation_recall: fraction of gold_chunk_ids that appear in Answer.citations
    averaged across answerable items that have non-empty gold_chunk_ids
  - citation_precision: fraction of Answer.citations whose chunk_id appears in
    gold_chunk_ids, averaged across answerable items with non-empty gold sets

EvalResult schema is frozen; do not change field names.
"""
from __future__ import annotations

from typing import TypedDict

from src.policylens.generate import Answer

from .golden import GoldenItem


class EvalResult(TypedDict):
    n_total: int
    n_answerable: int
    n_unanswerable: int
    abstention_accuracy: float   # unanswerable Qs correctly abstained
    answerable_accuracy: float   # answerable Qs that got an answer
    citation_recall: float       # fraction of gold chunks cited (requires gold_chunk_ids)
    citation_precision: float    # fraction of citations in gold set


def evaluate(golden: list[GoldenItem], answers: list[Answer]) -> EvalResult:
    """Score a list of Answer objects against their GoldenItems.

    Raises ValueError if lengths differ.
    Items with expected_answerable=True but empty gold_chunk_ids contribute to
    answerable_accuracy but are excluded from citation metrics (no ground truth).
    """
    if len(golden) != len(answers):
        raise ValueError(
            f"golden ({len(golden)}) and answers ({len(answers)}) must have equal length"
        )

    n_total = len(golden)

    # --- Split into answerable / unanswerable ---
    answerable_pairs = [
        (g, a) for g, a in zip(golden, answers) if g["expected_answerable"]
    ]
    unanswerable_pairs = [
        (g, a) for g, a in zip(golden, answers) if not g["expected_answerable"]
    ]

    n_answerable = len(answerable_pairs)
    n_unanswerable = len(unanswerable_pairs)

    # --- Abstention accuracy ---
    # Correct when the pipeline abstained (answerable=False) on an unanswerable item
    if n_unanswerable > 0:
        correct_abstentions = sum(
            1 for _g, a in unanswerable_pairs if not a["answerable"]
        )
        abstention_accuracy = correct_abstentions / n_unanswerable
    else:
        abstention_accuracy = 1.0  # vacuously true

    # --- Answerable accuracy ---
    # Correct when the pipeline produced an answer (answerable=True) for an answerable item
    if n_answerable > 0:
        correct_answers = sum(
            1 for _g, a in answerable_pairs if a["answerable"]
        )
        answerable_accuracy = correct_answers / n_answerable
    else:
        answerable_accuracy = 1.0  # vacuously true

    # --- Citation recall and precision ---
    # Only for answerable items with non-empty gold_chunk_ids and a real answer
    citation_recall_scores: list[float] = []
    citation_precision_scores: list[float] = []

    for g, a in answerable_pairs:
        gold_ids = set(g["gold_chunk_ids"])
        if not gold_ids:
            continue  # no ground truth for this item → skip citation metrics

        if not a["answerable"]:
            # Pipeline abstained on an answerable item → 0 recall, 0 precision
            citation_recall_scores.append(0.0)
            citation_precision_scores.append(0.0)
            continue

        cited_ids = {c["chunk_id"] for c in a["citations"]}

        # Recall: how many gold chunks were cited?
        recall = len(gold_ids & cited_ids) / len(gold_ids)
        citation_recall_scores.append(recall)

        # Precision: how many cited chunks are in the gold set?
        if cited_ids:
            precision = len(gold_ids & cited_ids) / len(cited_ids)
        else:
            precision = 0.0
        citation_precision_scores.append(precision)

    citation_recall = (
        sum(citation_recall_scores) / len(citation_recall_scores)
        if citation_recall_scores
        else 0.0
    )
    citation_precision = (
        sum(citation_precision_scores) / len(citation_precision_scores)
        if citation_precision_scores
        else 0.0
    )

    return EvalResult(
        n_total=n_total,
        n_answerable=n_answerable,
        n_unanswerable=n_unanswerable,
        abstention_accuracy=abstention_accuracy,
        answerable_accuracy=answerable_accuracy,
        citation_recall=citation_recall,
        citation_precision=citation_precision,
    )
