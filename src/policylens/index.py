"""Embed chunks + build/load Chroma index.

Filled in by index-engineer (Phase 1). The cli() entry point is wired up in
pyproject.toml so `uv run python -m policylens.index build` works after Phase 1.
"""
from __future__ import annotations

import sys

from .config import Config, DEFAULT_CONFIG


def build_index(chunks_path: str, cfg: Config = DEFAULT_CONFIG) -> None:
    """Embed all chunks and persist to cfg.index_dir.

    Filled in by index-engineer (Phase 1).
    """
    raise NotImplementedError("index-engineer fills this in during Phase 1")


def cli() -> None:
    """Entry point: `uv run python -m policylens.index build`."""
    if len(sys.argv) < 2 or sys.argv[1] != "build":
        print("Usage: python -m policylens.index build")
        sys.exit(1)
    cfg = DEFAULT_CONFIG
    chunks_path = f"{cfg.processed_dir}/chunks.jsonl"
    print(f"Building index from {chunks_path} → {cfg.index_dir} ...")
    build_index(chunks_path, cfg)
    print("Done.")


if __name__ == "__main__":
    cli()
