"""Retriever interface + implementations.

ChromaRetriever: production retriever backed by a persisted Chroma collection.
FixtureRetriever: fast keyword-overlap retriever for isolated dev/tests.
PgVectorRetriever: hybrid ANN + FTS retriever backed by pgvector on Postgres.

Use make_retriever(cfg) to get the right retriever for a given Config.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast

import numpy as np

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

        query_emb = np.asarray(self._model.encode([query])).tolist()

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
        ids = (results.get("ids") or [[]])[0]
        docs = (results.get("documents") or [[]])[0]
        metas = (results.get("metadatas") or [[]])[0]
        dists = (results.get("distances") or [[]])[0]

        for chunk_id, doc, meta, dist in zip(ids, docs, metas, dists):
            # Chroma cosine distance ∈ [0, 2]; convert to similarity ∈ [-1, 1]
            score = float(1.0 - dist)
            m = cast("dict[str, Any]", meta)  # chroma metadata values are loosely typed
            chunk = Chunk(
                chunk_id=chunk_id,
                policy_id=m["policy_id"],
                policy_name=m["policy_name"],
                section=m["section"],
                text=doc,
                char_start=int(m["char_start"]),
                char_end=int(m["char_end"]),
                source_url=m.get("source_url") or None,
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


# ---------------------------------------------------------------------------
# Factory — returns the correct Retriever for the configured backend.
# Chroma path is byte-identical to the pre-v2 behaviour; pgvector path
# reads the DSN from os.environ[cfg.db_url_env].
# ---------------------------------------------------------------------------


def make_retriever(cfg: Config) -> "ChromaRetriever | FixtureRetriever | object":
    """Construct and return the appropriate Retriever for cfg.retrieval_backend.

    "chroma"   → ChromaRetriever(cfg)       [default; no new env vars needed]
    "pgvector" → PgVectorRetriever(cfg)     [requires SUPABASE_DB_URL or cfg.db_url_env]

    Any other value raises ValueError.
    """
    if cfg.retrieval_backend == "chroma":
        return ChromaRetriever(cfg)
    elif cfg.retrieval_backend == "pgvector":
        from .pgvector import PgVectorRetriever  # local import to avoid hard dep at module level

        return PgVectorRetriever(cfg)
    else:
        raise ValueError(
            f"Unknown retrieval_backend {cfg.retrieval_backend!r}. "
            "Valid values: 'chroma', 'pgvector'."
        )
