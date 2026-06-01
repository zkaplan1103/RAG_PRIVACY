"""Eval seam stub — load_golden() maps PrivacyQA/PolicyQA onto our Answer schema.

Project C implements the full metrics harness. This file exists so the interface
is documented and importable now.

See docs/CONTRACTS.md §5.
"""
from __future__ import annotations

from typing import TypedDict


class GoldenItem(TypedDict):
    query: str
    policy_id: str
    expected_answerable: bool
    gold_chunk_ids: list[str]   # acceptable supporting chunks


def load_golden(path: str) -> list[GoldenItem]:
    """Map a PrivacyQA or PolicyQA annotation file onto GoldenItem records.

    Project C fills in this implementation.
    """
    raise NotImplementedError("Project C implements this in the eval harness")
