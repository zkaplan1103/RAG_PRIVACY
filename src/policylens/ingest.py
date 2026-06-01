"""Ingest + chunk OPP-115 policies into chunks.jsonl.

Filled in by data-engineer (Phase 1). This stub defines the public interface
and chunk schema so other agents can code against it now.

Chunk schema (TypedDict) — see docs/CONTRACTS.md §1.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from .config import Config


class Chunk(TypedDict):
    chunk_id: str       # stable, e.g. "google_pp::sec3::c07"
    policy_id: str      # e.g. "google_privacy_policy"
    policy_name: str    # human label
    section: str        # heading/category this chunk falls under
    text: str           # clause text — plain text, no HTML
    char_start: int     # byte offset into source doc
    char_end: int
    source_url: str | None


def ingest(raw_dir: str, out_path: str, cfg: Config) -> int:
    """Parse raw OPP-115 HTML/XML into Chunk records and write chunks.jsonl.

    Returns the number of chunks written.
    Raises NotImplementedError until data-engineer fills this in.
    """
    raise NotImplementedError("data-engineer fills this in during Phase 1")


def iter_chunks(chunks_path: str):
    """Yield Chunk dicts from a chunks.jsonl file."""
    with open(chunks_path) as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
