"""Eval metrics interface — Project C implements these.

Interface contract:
  evaluate(golden, answers) -> EvalResult

To run an eval:
  1. Load golden items:   items = load_golden("data/raw/privacyqa/data/policy_test_data.csv")
  2. Run the RAG system:  answers = [answer(g["query"], g["policy_id"], retriever, cfg) for g in items]
  3. Score:               result = evaluate(items, answers)

Metrics Project C should implement:
  - abstention_accuracy: % of unanswerable queries correctly abstained
  - answerable_accuracy: % of answerable queries that produced an answer
  - citation_recall@k:   fraction of gold_chunk_ids appearing in returned citations
  - citation_precision:  fraction of cited chunks in the gold set
"""
from __future__ import annotations

from typing import TypedDict

from .golden import GoldenItem
from src.policylens.generate import Answer


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

    Project C implements this. Stub raises NotImplementedError.
    """
    raise NotImplementedError("Project C implements the metrics harness")
