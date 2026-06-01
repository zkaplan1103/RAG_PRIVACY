"""Retriever interface + stub implementation.

Filled in by index-engineer (Phase 1). This file defines the Protocol so
rag-engineer can code against it with a fake retriever.

Retriever interface — see docs/CONTRACTS.md §2.
"""
from __future__ import annotations

from typing import Protocol, TypedDict

from .config import Config
from .ingest import Chunk


class RetrievedChunk(TypedDict):
    chunk: Chunk
    score: float  # cosine similarity, higher = better


class Retriever(Protocol):
    def retrieve(self, query: str, policy_id: str, k: int = 5) -> list[RetrievedChunk]:
        """Return at most k chunks for policy_id, sorted by score desc."""
        ...


class ChromaRetriever:
    """Production retriever backed by a persisted Chroma collection.

    Filled in by index-engineer (Phase 1).
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._client = None  # lazy-init; index-engineer wires this up

    def retrieve(self, query: str, policy_id: str, k: int = 5) -> list[RetrievedChunk]:
        raise NotImplementedError("index-engineer fills this in during Phase 1")


class FixtureRetriever:
    """Loads a chunks_sample.jsonl fixture and does exact-text search.

    Used by rag-engineer and ui-engineer for isolated dev (no real index needed).
    """

    def __init__(self, fixture_path: str = "tests/fixtures/chunks_sample.jsonl") -> None:
        import json
        from pathlib import Path

        self._chunks: list[Chunk] = []
        p = Path(fixture_path)
        if p.exists():
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._chunks.append(json.loads(line))

    def retrieve(self, query: str, policy_id: str, k: int = 5) -> list[RetrievedChunk]:
        """Return chunks matching policy_id, scored by naive keyword overlap."""
        hits = [c for c in self._chunks if c["policy_id"] == policy_id]
        query_words = set(query.lower().split())

        def _score(chunk: Chunk) -> float:
            words = set(chunk["text"].lower().split())
            overlap = len(query_words & words)
            return overlap / max(len(query_words), 1)

        scored = [{"chunk": c, "score": _score(c)} for c in hits]
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:k]
