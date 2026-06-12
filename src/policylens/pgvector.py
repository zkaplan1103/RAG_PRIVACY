"""PgVectorRetriever — hybrid ANN + FTS retrieval backed by pgvector on Postgres.

Implements the frozen Retriever protocol (docs/CONTRACTS.md §2) exactly.
Hybrid search fuses cosine-ANN and FTS legs via Reciprocal Rank Fusion (RRF).
Optional local cross-encoder reranking (BAAI/bge-reranker-base) rescores
fused candidates; the reranker score is rescaled to [0, 1] so the
score_floor abstention semantics (CONTRACTS §3) keep working unchanged.

Connection pool:
  - Uses psycopg v3 ConnectionPool.
  - DSN is read at construction time from the env var named by Config.db_url_env.
  - Raises RuntimeError if the env var is absent (fail-fast; never silently
    degrade to empty results from a misconfigured pgvector retriever).

Usage:
  from policylens.pgvector import PgVectorRetriever
  r = PgVectorRetriever(cfg)          # reads DSN from env
  chunks = r.retrieve(query, policy_id, k=5)
  r.close()                           # release pool

The retriever is also usable as a context manager:
  with PgVectorRetriever(cfg) as r:
      chunks = r.retrieve(query, policy_id)
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from .config import Config
from .ingest import Chunk
from .retrieve import RetrievedChunk

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# RRF helper
# ---------------------------------------------------------------------------

def _rrf_fuse(
    ann_hits: list[tuple[str, float]],   # (chunk_id, cosine_score) sorted desc
    fts_hits: list[tuple[str, float]],   # (chunk_id, fts_rank_score) sorted desc
    rrf_k: int,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion of two ranked lists.

    score(d) = sum_over_legs( 1 / (rrf_k + rank_in_leg) )
    rank is 1-based; documents absent from a leg get no contribution from it.
    Returns list of (chunk_id, rrf_score) sorted by rrf_score descending.
    """
    scores: dict[str, float] = {}
    for rank_0, (cid, _) in enumerate(ann_hits):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank_0 + 1)
    for rank_0, (cid, _) in enumerate(fts_hits):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank_0 + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# Score rescaling helper
# ---------------------------------------------------------------------------

def _rescale_to_unit(scores: list[float]) -> list[float]:
    """Linearly rescale a list of floats to [0, 1].

    If all scores are equal the list is returned as-is (avoid divide-by-zero).
    This is applied to raw cross-encoder logit outputs so that score_floor
    comparisons remain meaningful.
    """
    if not scores:
        return scores
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return [1.0] * len(scores)
    return [(s - lo) / (hi - lo) for s in scores]


# ---------------------------------------------------------------------------
# PgVectorRetriever
# ---------------------------------------------------------------------------

class PgVectorRetriever:
    """Hybrid pgvector retriever implementing the frozen Retriever protocol."""

    def __init__(
        self,
        cfg: Config,
        *,
        _pool: Any = None,  # injection point for unit tests
    ) -> None:
        """Initialise the connection pool and (optionally) the reranker.

        Parameters
        ----------
        cfg:
            Config instance. DSN is read from os.environ[cfg.db_url_env].
        _pool:
            Inject a fake pool for unit tests; skips env-var lookup and
            psycopg import entirely.
        """
        self.cfg = cfg
        self._reranker: Any = None

        if _pool is not None:
            # Injected pool (unit tests) — skip real psycopg setup.
            self._pool = _pool
        else:
            dsn = os.environ.get(cfg.db_url_env)
            if not dsn:
                raise RuntimeError(
                    f"PgVectorRetriever requires env var {cfg.db_url_env!r} to be set. "
                    "Set it to a valid Postgres DSN (e.g. postgresql://user:pass@host/db)."
                )
            from psycopg_pool import ConnectionPool  # type: ignore[import-untyped]

            self._pool = ConnectionPool(dsn, min_size=1, max_size=5, open=True)

        # Load cross-encoder reranker lazily only when actually needed.
        # Delay import so that the module can be imported without sentence-transformers
        # installed (it IS installed in this project, but the import-time side-effect
        # of model download is deferred until first retrieve() call with rerank_enabled).
        if cfg.rerank_enabled:
            self._reranker = _load_reranker(cfg.rerank_model)

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "PgVectorRetriever":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying connection pool."""
        if hasattr(self._pool, "close"):
            self._pool.close()

    # ------------------------------------------------------------------
    # Core retrieval
    # ------------------------------------------------------------------

    def retrieve(self, query: str, policy_id: str, k: int = 5) -> list[RetrievedChunk]:
        """Hybrid retrieval: cosine ANN + FTS fused via RRF, then optionally reranked.

        Both legs are scoped to policy_id.
        Final list is sorted by score desc, at most k items.
        """
        cfg = self.cfg
        n_candidates = cfg.fts_candidates  # pull this many from each leg

        # 1. Embed query
        embedding = _embed_query(query, cfg.embed_model)

        # 2. ANN leg (cosine)
        ann_rows = self._run_ann(policy_id, embedding, n_candidates)
        # ann_rows: list of (chunk_id, cosine_score, row_dict)

        # 3. FTS leg
        fts_rows = self._run_fts(policy_id, query, n_candidates)
        # fts_rows: list of (chunk_id, ts_rank, row_dict)

        # Build chunk data lookup from both legs (chunk_id -> row_dict)
        chunk_data: dict[str, dict[str, Any]] = {}
        for cid, _score, row in ann_rows:
            chunk_data[cid] = row
        for cid, _score, row in fts_rows:
            if cid not in chunk_data:
                chunk_data[cid] = row

        # 4. RRF fusion
        ann_ranked = [(cid, s) for cid, s, _ in ann_rows]
        fts_ranked = [(cid, s) for cid, s, _ in fts_rows]
        fused = _rrf_fuse(ann_ranked, fts_ranked, cfg.hybrid_rrf_k)

        if not fused:
            return []

        # 5. Optionally rerank
        rerank_n = cfg.rerank_top_n if cfg.rerank_enabled else k
        candidates = fused[:max(rerank_n, k)]  # take top candidates for reranking

        if cfg.rerank_enabled and self._reranker is not None and candidates:
            # Filter to only candidates that have data (should always be all of them)
            valid_cids = [cid for cid, _ in candidates if cid in chunk_data]
            texts = [chunk_data[cid]["text"] for cid in valid_cids]
            # Pair: (query, chunk_text)
            pairs = [(query, t) for t in texts]
            raw_scores: list[float] = self._reranker.predict(pairs).tolist()
            rescaled = _rescale_to_unit(raw_scores)

            # Rebuild candidates list with reranker scores (lengths match)
            candidates = list(zip(valid_cids, rescaled))
            candidates.sort(key=lambda x: x[1], reverse=True)
        else:
            # No reranker — cap at k and use RRF scores directly.
            # Rescale RRF scores (which are small positive numbers) to [0, 1]
            # so score_floor checks remain meaningful.
            rrf_scores = [s for _, s in candidates]
            rrf_rescaled = _rescale_to_unit(rrf_scores)
            candidates = [(cid, sc) for (cid, _), sc in zip(candidates, rrf_rescaled)]

        # 6. Build final list
        results: list[RetrievedChunk] = []
        for cid, score in candidates[:k]:
            if cid not in chunk_data:
                continue
            row = chunk_data[cid]
            chunk = Chunk(
                chunk_id=cid,
                policy_id=row["policy_id"],
                policy_name=row["policy_name"],
                section=row["section"],
                text=row["text"],
                char_start=row["char_start"],
                char_end=row["char_end"],
                source_url=row.get("source_url"),
            )
            results.append(RetrievedChunk(chunk=chunk, score=score))

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    # ------------------------------------------------------------------
    # Private DB helpers
    # ------------------------------------------------------------------

    def _run_ann(
        self, policy_id: str, embedding: list[float], n: int
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Cosine ANN query scoped to policy_id. Returns (chunk_id, score, row) list."""
        sql = """
            SELECT
                chunk_id, policy_id, policy_name, section, text,
                char_start, char_end, source_url,
                1 - (embedding <=> %s::vector) AS score
            FROM chunks
            WHERE policy_id = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        rows = []
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (embedding, policy_id, embedding, n))
                for rec in cur.fetchall():
                    cid = rec[0]
                    score = float(rec[8])
                    row = {
                        "policy_id": rec[1],
                        "policy_name": rec[2],
                        "section": rec[3],
                        "text": rec[4],
                        "char_start": rec[5],
                        "char_end": rec[6],
                        "source_url": rec[7],
                    }
                    rows.append((cid, score, row))
        return rows

    def _run_fts(
        self, policy_id: str, query: str, n: int
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Full-text search scoped to policy_id using plainto_tsquery.

        Returns list of (chunk_id, ts_rank, row_dict).
        """
        sql = """
            SELECT
                chunk_id, policy_id, policy_name, section, text,
                char_start, char_end, source_url,
                ts_rank(tsv, plainto_tsquery('english', %s)) AS rank
            FROM chunks
            WHERE policy_id = %s
              AND tsv @@ plainto_tsquery('english', %s)
            ORDER BY rank DESC
            LIMIT %s
        """
        rows = []
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (query, policy_id, query, n))
                for rec in cur.fetchall():
                    cid = rec[0]
                    rank = float(rec[8])
                    row = {
                        "policy_id": rec[1],
                        "policy_name": rec[2],
                        "section": rec[3],
                        "text": rec[4],
                        "char_start": rec[5],
                        "char_end": rec[6],
                        "source_url": rec[7],
                    }
                    rows.append((cid, rank, row))
        return rows


# ---------------------------------------------------------------------------
# Module-level helpers (importable for tests)
# ---------------------------------------------------------------------------

_EMBED_CACHE: dict[str, Any] = {}  # model_name -> SentenceTransformer


def _embed_query(query: str, model_name: str) -> list[float]:
    """Embed a single query string using sentence-transformers (cached model)."""
    from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]

    if model_name not in _EMBED_CACHE:
        _EMBED_CACHE[model_name] = SentenceTransformer(model_name)
    model = _EMBED_CACHE[model_name]
    vec: list[float] = model.encode([query], normalize_embeddings=True)[0].tolist()
    return vec


_RERANKER_CACHE: dict[str, Any] = {}  # model_name -> CrossEncoder


def _load_reranker(model_name: str) -> Any:
    """Load (and cache) a CrossEncoder reranker model."""
    from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]

    if model_name not in _RERANKER_CACHE:
        _RERANKER_CACHE[model_name] = CrossEncoder(model_name)
    return _RERANKER_CACHE[model_name]
