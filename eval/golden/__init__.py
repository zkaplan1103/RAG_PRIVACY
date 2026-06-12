"""Golden eval set package — GoldenItem schema and load_golden() entry point.

GoldenItem (v1 schema, CONTRACTS.md §5) is the interface consumed by
eval/metrics.py evaluate().  GoldenItemV2 (CONTRACTS.md §9) extends it with
the id and reference_answer fields and lives in build_golden.py.

load_golden() loads a PrivacyQA CSV into GoldenItem records for backward compat.
For the v2 golden set (golden_v1.jsonl) use eval/ragas/run_ragas.py's load_golden.
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
    gold_chunk_ids: list[str]  # empty until mapped to OPP-115 chunk IDs


def load_golden(path: str) -> list[GoldenItem]:
    """Load a PrivacyQA train or test CSV and return one GoldenItem per unique query.

    PrivacyQA format (tab-separated, with header):
      train: Folder | DocID | QueryID | SentID | Split | Query | Segment | Label
      test:  Folder | DocID | QueryID | SentID | Split | Query | Segment | Any_Relevant | Ann1..6

    A query is answerable if at least one segment is labelled "Relevant" (case-insensitive).
    gold_chunk_ids is always empty — aligned via build_golden.py for OPP-115 chunk IDs.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Golden file not found: {path}")

    queries: dict[str, dict] = {}
    relevance: dict[str, bool] = defaultdict(bool)

    with open(p, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            qid = row.get("QueryID", "").strip()
            if not qid:
                continue

            if qid not in queries:
                doc_id = row.get("DocID", "").strip()
                queries[qid] = {
                    "query": row.get("Query", "").strip(),
                    "policy_id": "",
                    "_doc_id": doc_id,
                }

            label = row.get("Label", "").strip().lower()
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
