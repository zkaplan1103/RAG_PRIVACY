"""Eval seam — load_golden() maps PrivacyQA annotations onto GoldenItem records.

This module provides the interface Project C plugs metrics into.
load_golden() is implemented here for PrivacyQA; gold_chunk_ids mapping
to OPP-115 chunk IDs is left for Project C (the corpora use different docs).

See docs/CONTRACTS.md §5.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import TypedDict


class GoldenItem(TypedDict):
    query: str
    policy_id: str          # best-effort slug; empty string if no OPP-115 match
    expected_answerable: bool
    gold_chunk_ids: list[str]  # empty until Project C maps PrivacyQA → OPP-115 chunks


def load_golden(path: str) -> list[GoldenItem]:
    """Load a PrivacyQA train or test CSV and return one GoldenItem per unique query.

    PrivacyQA format (tab-separated, with header):
      train: Folder | DocID | QueryID | SentID | Split | Query | Segment | Label
      test:  Folder | DocID | QueryID | SentID | Split | Query | Segment | Any_Relevant | Ann1..6

    A query is answerable if at least one segment is labelled "Relevant" (case-insensitive).
    gold_chunk_ids is always empty — Project C fills this by aligning PrivacyQA
    app-policy segments with OPP-115 chunk texts.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Golden file not found: {path}")

    # query_id → {query, policy_id, any_relevant}
    queries: dict[str, dict] = {}
    relevance: dict[str, bool] = defaultdict(bool)

    with open(p, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            qid = row.get("QueryID", "").strip()
            if not qid:
                continue

            if qid not in queries:
                # policy_id: derive from DocID slug (not an OPP-115 id — left for Project C)
                doc_id = row.get("DocID", "").strip()
                queries[qid] = {
                    "query": row.get("Query", "").strip(),
                    "policy_id": "",   # Project C maps this to an OPP-115 policy_id
                    "_doc_id": doc_id,
                }

            # Train set: Label column
            label = row.get("Label", "").strip().lower()
            # Test set: Any_Relevant column (aggregated across annotators)
            any_rel = row.get("Any_Relevant", "").strip().lower()
            if label == "relevant" or any_rel == "relevant":
                relevance[qid] = True

    items: list[GoldenItem] = []
    for qid, meta in queries.items():
        if not meta["query"]:
            continue
        items.append(GoldenItem(
            query=meta["query"],
            policy_id=meta["policy_id"],
            expected_answerable=relevance[qid],
            gold_chunk_ids=[],
        ))

    return items
