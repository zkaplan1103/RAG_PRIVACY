"""Embed chunks + build/load Chroma index.

Build command: uv run python -m policylens.index build
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .config import Config, DEFAULT_CONFIG


def build_index(chunks_path: str, cfg: Config = DEFAULT_CONFIG) -> None:
    """Embed all chunks from chunks_path and persist a Chroma collection to cfg.index_dir.

    Skips re-embedding if the collection already contains documents (cache).
    """
    import chromadb
    from sentence_transformers import SentenceTransformer

    Path(cfg.index_dir).mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=cfg.index_dir)
    collection = client.get_or_create_collection(
        name="policylens",
        metadata={"hnsw:space": "cosine"},
    )

    if collection.count() > 0:
        print(f"Index already built ({collection.count()} docs). Skipping.")
        return

    # Lazy import here so tests can monkeypatch before import
    from .ingest import iter_chunks

    chunks = list(iter_chunks(chunks_path))
    if not chunks:
        print("No chunks found — nothing to index.")
        return

    print(f"Loading embed model {cfg.embed_model} …")
    model = SentenceTransformer(cfg.embed_model)

    texts = [c["text"] for c in chunks]
    ids = [c["chunk_id"] for c in chunks]

    # Metadata: Chroma requires flat primitive values
    metadatas = [
        {
            "policy_id": c["policy_id"],
            "policy_name": c["policy_name"],
            "section": c["section"],
            "char_start": c["char_start"],
            "char_end": c["char_end"],
            "source_url": c["source_url"] or "",
        }
        for c in chunks
    ]

    print(f"Embedding {len(texts)} chunks …")
    batch_size = 128
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        embs = model.encode(batch, show_progress_bar=False).tolist()
        all_embeddings.extend(embs)
        if (i // batch_size) % 5 == 0:
            print(f"  {min(i + batch_size, len(texts))}/{len(texts)}")

    collection.add(
        ids=ids,
        embeddings=all_embeddings,
        documents=texts,
        metadatas=metadatas,
    )
    print(f"Indexed {collection.count()} chunks → {cfg.index_dir}")


def cli() -> None:
    """Entry point: uv run python -m policylens.index build"""
    if len(sys.argv) < 2 or sys.argv[1] != "build":
        print("Usage: python -m policylens.index build")
        sys.exit(1)
    cfg = DEFAULT_CONFIG
    chunks_path = f"{cfg.processed_dir}/chunks.jsonl"
    print(f"Building index from {chunks_path} → {cfg.index_dir} …")
    build_index(chunks_path, cfg)
    print("Done.")


if __name__ == "__main__":
    cli()
