"""Backfill / migration script — loads chunks.jsonl into pgvector.

Usage
-----
    uv run python -m policylens.migrate_pgvector \\
        [--chunks data/processed/chunks.jsonl] \\
        [--batch-size 100] \\
        [--no-reuse-chroma]

The script is fully **idempotent**: each chunk is upserted by primary key
(chunk_id), so re-running it is safe even if the table is partially populated.

Embedding strategy (avoid re-embedding 2393 chunks)
-----------------------------------------------------
1. If a Chroma store exists at Config.index_dir with a "policylens" collection,
   retrieve stored embeddings from it (lookup by id).
2. Any chunks missing from Chroma are embedded fresh using the same
   sentence-transformers model (BAAI/bge-small-en-v1.5, 384-dim).

This worktree has no Chroma data (data/ is git-ignored), so the script
handles "no chroma cache" by embedding all chunks fresh — same code path,
different branch.

Database connection
-------------------
DSN is read from os.environ[cfg.db_url_env] (default env var: SUPABASE_DB_URL).
The SQL schema must already exist (run infra/sql/001_init.sql first).

NOTE: Do not run this against a live DB until SETUP_TASKS.md step N.
      This script is designed and tested without any real DB connection.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG, Config

# ---------------------------------------------------------------------------
# Batch upsert
# ---------------------------------------------------------------------------

_UPSERT_SQL = """
INSERT INTO chunks (
    chunk_id, policy_id, policy_name, section, text,
    char_start, char_end, source_url, embedding
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s::vector
)
ON CONFLICT (chunk_id) DO UPDATE SET
    policy_id   = EXCLUDED.policy_id,
    policy_name = EXCLUDED.policy_name,
    section     = EXCLUDED.section,
    text        = EXCLUDED.text,
    char_start  = EXCLUDED.char_start,
    char_end    = EXCLUDED.char_end,
    source_url  = EXCLUDED.source_url,
    embedding   = EXCLUDED.embedding
"""


def _try_load_chroma_embeddings(
    index_dir: str, chunk_ids: list[str]
) -> dict[str, list[float]]:
    """Attempt to load pre-computed embeddings from a Chroma store.

    Returns a dict of chunk_id -> embedding vector.
    Returns an empty dict if the Chroma store is absent or empty.
    """
    try:
        import chromadb  # type: ignore[import-untyped]
    except ImportError:
        return {}

    try:
        client = chromadb.PersistentClient(path=index_dir)
        collection = client.get_collection("policylens")
        if collection.count() == 0:
            return {}

        # Chroma batch get is limited; do it in batches of 1000.
        result: dict[str, list[float]] = {}
        batch_size = 1000
        for i in range(0, len(chunk_ids), batch_size):
            batch = chunk_ids[i : i + batch_size]
            res = collection.get(ids=batch, include=["embeddings"])
            for cid, emb in zip(res["ids"], res["embeddings"] or []):  # type: ignore[arg-type]
                result[cid] = list(emb)  # type: ignore[arg-type]
        print(
            f"  [chroma] Loaded {len(result)}/{len(chunk_ids)} cached embeddings.",
            flush=True,
        )
        return result
    except Exception as exc:
        print(f"  [chroma] Could not load cache ({exc}); will embed fresh.", flush=True)
        return {}


def _embed_batch(
    texts: list[str], model_name: str, model_cache: dict[str, Any]
) -> list[list[float]]:
    """Embed a batch of texts. Model is cached in model_cache by model_name."""
    from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]

    if model_name not in model_cache:
        print(f"  [embed] Loading model {model_name!r} ...", flush=True)
        model_cache[model_name] = SentenceTransformer(model_name)
    model = model_cache[model_name]
    vectors = model.encode(texts, batch_size=64, show_progress_bar=True, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


# ---------------------------------------------------------------------------
# Main migration function
# ---------------------------------------------------------------------------

def migrate(
    chunks_path: str = "data/processed/chunks.jsonl",
    batch_size: int = 100,
    cfg: Config | None = None,
    reuse_chroma: bool = True,
    *,
    _conn: Any = None,  # injection point for tests (fake connection)
) -> int:
    """Run the migration / backfill.

    Parameters
    ----------
    chunks_path:
        Path to the JSONL file produced by ingest.py.
    batch_size:
        Number of rows to upsert per DB transaction.
    cfg:
        Config to use. Defaults to DEFAULT_CONFIG.
    reuse_chroma:
        If True (default), try to load embeddings from the Chroma cache.
    _conn:
        Inject a fake psycopg connection for unit tests; skips env-var
        lookup and real DB connection entirely.

    Returns
    -------
    int
        Number of chunks upserted.
    """
    if cfg is None:
        cfg = DEFAULT_CONFIG

    p = Path(chunks_path)
    if not p.exists():
        raise FileNotFoundError(
            f"chunks file not found: {chunks_path!r}. "
            "Pass --chunks pointing to data/processed/chunks.jsonl "
            "or the fixture at tests/fixtures/chunks_sample.jsonl."
        )

    # --- Load all chunks (streaming, never slurp the whole thing at once for large files)
    print(f"Reading chunks from {chunks_path!r} ...", flush=True)
    chunks: list[dict[str, Any]] = []
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    print(f"  Loaded {len(chunks)} chunks.", flush=True)

    # --- Attempt to reuse Chroma embeddings
    cached_embeddings: dict[str, list[float]] = {}
    if reuse_chroma:
        all_ids = [c["chunk_id"] for c in chunks]
        cached_embeddings = _try_load_chroma_embeddings(cfg.index_dir, all_ids)

    # --- Compute missing embeddings
    missing = [c for c in chunks if c["chunk_id"] not in cached_embeddings]
    model_cache: dict[str, Any] = {}
    if missing:
        print(f"  Embedding {len(missing)} chunks (not in Chroma cache) ...", flush=True)
        texts = [c["text"] for c in missing]
        fresh_vecs = _embed_batch(texts, cfg.embed_model, model_cache)
        for chunk, vec in zip(missing, fresh_vecs):
            cached_embeddings[chunk["chunk_id"]] = vec
    else:
        print("  All embeddings loaded from Chroma cache. No re-embedding needed.", flush=True)

    # --- Upsert into pgvector
    total_upserted = 0

    if _conn is not None:
        # Injected fake connection (unit tests) — run full upsert logic against it.
        conn = _conn
        _do_upsert(conn, chunks, cached_embeddings, batch_size)
        total_upserted = len(chunks)
    else:
        dsn = os.environ.get(cfg.db_url_env)
        if not dsn:
            raise RuntimeError(
                f"env var {cfg.db_url_env!r} is not set. "
                "Set it to a valid Postgres DSN before running the migration."
            )
        import psycopg  # type: ignore[import-untyped]

        print(f"Connecting to database (via {cfg.db_url_env}) ...", flush=True)
        with psycopg.connect(dsn) as conn:
            _do_upsert(conn, chunks, cached_embeddings, batch_size)
            total_upserted = len(chunks)
            conn.commit()

    print(f"Done. {total_upserted} chunks upserted.", flush=True)
    return total_upserted


def _do_upsert(
    conn: Any,
    chunks: list[dict[str, Any]],
    embeddings: dict[str, list[float]],
    batch_size: int,
) -> None:
    """Execute batched upserts against an open psycopg connection."""
    total = len(chunks)
    for start in range(0, total, batch_size):
        batch = chunks[start : start + batch_size]
        rows = []
        for c in batch:
            cid = c["chunk_id"]
            emb = embeddings.get(cid)
            if emb is None:
                print(f"  [WARN] No embedding for {cid!r}; skipping.", flush=True)
                continue
            rows.append((
                cid,
                c["policy_id"],
                c["policy_name"],
                c["section"],
                c["text"],
                c["char_start"],
                c["char_end"],
                c.get("source_url"),
                emb,
            ))
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, rows)
        end = min(start + batch_size, total)
        print(f"  Upserted {end}/{total} ...", flush=True)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill chunks.jsonl into pgvector. Reads DSN from env var."
    )
    parser.add_argument(
        "--chunks",
        default="data/processed/chunks.jsonl",
        help="Path to chunks.jsonl (default: data/processed/chunks.jsonl)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Rows per upsert batch (default: 100)",
    )
    parser.add_argument(
        "--no-reuse-chroma",
        action="store_true",
        help="Skip Chroma embedding cache; always embed fresh.",
    )
    args = parser.parse_args()

    try:
        count = migrate(
            chunks_path=args.chunks,
            batch_size=args.batch_size,
            reuse_chroma=not args.no_reuse_chroma,
        )
        print(f"\nMigration complete: {count} chunks in pgvector.")
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
