"""Retriever interface + implementations.

ChromaRetriever: production retriever backed by a persisted Chroma collection.
FixtureRetriever: fast keyword-overlap retriever for isolated dev/tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, TypedDict

from .config import Config
from .ingest import Chunk


class RetrievedChunk(TypedDict):
    chunk: Chunk
    score: float  # cosine similarity; higher = better


class Retriever(Protocol):
    def retrieve(self, query: str, policy_id: str, k: int = 5) -> list[RetrievedChunk]:
        """Return at most k chunks for policy_id, sorted by score desc."""
        ...


class ChromaRetriever:
    """Production retriever backed by a persisted Chroma collection."""

    def __init__(self, cfg: Config) -> None:
        import chromadb
        from sentence_transformers import SentenceTransformer

        self.cfg = cfg
        self._client = chromadb.PersistentClient(path=cfg.index_dir)
        self._collection = self._client.get_or_create_collection(
            name="policylens",
            metadata={"hnsw:space": "cosine"},
        )
        self._model = SentenceTransformer(cfg.embed_model)

    def retrieve(self, query: str, policy_id: str, k: int = 5) -> list[RetrievedChunk]:
        """Embed query, search Chroma filtered by policy_id, return top-k."""
        if self._collection.count() == 0:
            return []

        query_emb = self._model.encode([query]).tolist()

        try:
            results = self._collection.query(
                query_embeddings=query_emb,
                n_results=min(k, self._collection.count()),
                where={"policy_id": policy_id},
                include=["documents", "metadatas", "distances"],
            )
        except Exception:
            return []

        hits: list[RetrievedChunk] = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]

        for chunk_id, doc, meta, dist in zip(ids, docs, metas, dists):
            # Chroma cosine distance ∈ [0, 2]; convert to similarity ∈ [-1, 1]
            score = float(1.0 - dist)
            chunk = Chunk(
                chunk_id=chunk_id,
                policy_id=meta["policy_id"],
                policy_name=meta["policy_name"],
                section=meta["section"],
                text=doc,
                char_start=int(meta["char_start"]),
                char_end=int(meta["char_end"]),
                source_url=meta.get("source_url") or None,
            )
            hits.append(RetrievedChunk(chunk=chunk, score=score))

        hits.sort(key=lambda x: x["score"], reverse=True)
        return hits


class FixtureRetriever:
    """Loads a chunks_sample.jsonl fixture and does keyword-overlap scoring.

    Used for isolated dev — no real index needed.
    """

    def __init__(self, fixture_path: str = "tests/fixtures/chunks_sample.jsonl") -> None:
        self._chunks: list[Chunk] = []
        p = Path(fixture_path)
        if p.exists():
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._chunks.append(json.loads(line))

    def retrieve(self, query: str, policy_id: str, k: int = 5) -> list[RetrievedChunk]:
        hits = [c for c in self._chunks if c["policy_id"] == policy_id]
        query_words = set(query.lower().split())

        def _score(chunk: Chunk) -> float:
            words = set(chunk["text"].lower().split())
            overlap = len(query_words & words)
            return overlap / max(len(query_words), 1)

        scored = [RetrievedChunk(chunk=c, score=_score(c)) for c in hits]
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:k]
