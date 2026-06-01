"""Ingest + chunk OPP-115 policies into chunks.jsonl.

OPP-115 format:
- sanitized_policies/*.html  — policy text with segments separated by "|||"
- annotations/*.csv          — no header; columns:
    0: annotation_id
    1: batch_id
    2: annotator_id
    3: policy_id  (numeric, matches filename prefix)
    4: segment_id (0-indexed)
    5: category   (data-practice label, e.g. "First Party Collection/Use")
    6: attributes_json
    7: date
    8: url (optional)

Strategy: split each policy into its "|||"-delimited segments, then map each
segment to its majority data-practice category from the annotations CSV.
Segments with no annotation get category "Other". Adjacent segments of the
same category are merged into one chunk; chunks exceeding MAX_CHARS are split
at sentence boundaries.
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import TypedDict

from .config import Config

MAX_CHARS = 1600  # ~400 tokens at ~4 chars/token


class Chunk(TypedDict):
    chunk_id: str
    policy_id: str
    policy_name: str
    section: str
    text: str
    char_start: int
    char_end: int
    source_url: str | None


def _slug(text: str) -> str:
    """Convert a category name to a URL-safe slug."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _clean_html(html: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _split_at_sentences(text: str, max_chars: int) -> list[str]:
    """Split text into pieces no longer than max_chars, breaking at '. '."""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    # find sentence boundaries: period followed by space + uppercase (or end)
    boundaries = [m.end() for m in re.finditer(r"\.\s+(?=[A-Z])", text)]
    start = 0
    current_end = 0
    for boundary in boundaries:
        if boundary - start > max_chars and current_end > start:
            pieces.append(text[start:current_end].strip())
            start = current_end
        current_end = boundary
    tail = text[start:].strip()
    if tail:
        # if tail is still too long, hard-split at max_chars
        while len(tail) > max_chars:
            pieces.append(tail[:max_chars].strip())
            tail = tail[max_chars:].strip()
        if tail:
            pieces.append(tail)
    return [p for p in pieces if p]


def _load_segment_categories(annotation_path: Path) -> dict[int, str]:
    """Return {segment_id: majority_category} for one policy's annotation CSV."""
    seg_cats: dict[int, list[str]] = {}
    try:
        with open(annotation_path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.reader(f):
                if len(row) < 6:
                    continue
                try:
                    seg_id = int(row[4])
                except ValueError:
                    continue
                category = row[5].strip() or "Other"
                seg_cats.setdefault(seg_id, []).append(category)
    except FileNotFoundError:
        return {}
    return {seg_id: Counter(cats).most_common(1)[0][0] for seg_id, cats in seg_cats.items()}


def _parse_policy(html_path: Path, annotation_path: Path) -> list[tuple[str, str, int, int]]:
    """Parse one policy file into (category, text, char_start, char_end) tuples.

    Uses the full policy text (with "|||" stripped) as the coordinate space for
    char_start/char_end so the offsets are stable and unique within a policy.
    """
    raw_html = html_path.read_text(encoding="utf-8", errors="replace")
    # The sanitized format separates segments with "|||"
    raw_segments = raw_html.split("|||")
    segments = [_clean_html(s) for s in raw_segments]

    seg_cats = _load_segment_categories(annotation_path)

    # Build full clean text with segment positions tracked
    full_text = " ".join(s for s in segments if s)
    # Recompute positions per segment in the joined text
    results: list[tuple[str, str, int, int]] = []
    pos = 0
    for i, seg_text in enumerate(segments):
        if not seg_text:
            continue
        category = seg_cats.get(i, "Other")
        # Find the segment in full_text starting from pos
        idx = full_text.find(seg_text, pos)
        if idx == -1:
            idx = pos  # fallback
        char_start = idx
        char_end = idx + len(seg_text)
        pos = char_end
        results.append((category, seg_text, char_start, char_end))

    return results


def _merge_and_chunk(
    segments: list[tuple[str, str, int, int]],
    policy_id: str,
    policy_name: str,
) -> list[Chunk]:
    """Merge adjacent same-category segments and split oversized chunks."""
    chunks: list[Chunk] = []
    cat_counters: dict[str, int] = {}

    # Merge adjacent same-category segments
    merged: list[tuple[str, str, int, int]] = []
    for cat, text, cs, ce in segments:
        if merged and merged[-1][0] == cat:
            prev_cat, prev_text, prev_cs, prev_ce = merged[-1]
            merged[-1] = (cat, prev_text + " " + text, prev_cs, ce)
        else:
            merged.append((cat, text, cs, ce))

    for cat, text, char_start, char_end in merged:
        if not text.strip():
            continue
        pieces = _split_at_sentences(text, MAX_CHARS)
        for piece in pieces:
            if not piece.strip():
                continue
            slug = _slug(cat)
            idx = cat_counters.get(slug, 0)
            cat_counters[slug] = idx + 1
            # Recompute char offsets for split pieces within the merged text
            piece_start = text.find(piece)
            if piece_start == -1:
                piece_start = 0
            abs_start = char_start + piece_start
            abs_end = abs_start + len(piece)
            chunks.append(Chunk(
                chunk_id=f"{policy_id}::{slug}::c{idx:03d}",
                policy_id=policy_id,
                policy_name=policy_name,
                section=cat,
                text=piece,
                char_start=abs_start,
                char_end=abs_end,
                source_url=None,
            ))

    return chunks


def ingest(raw_dir: str, out_path: str, cfg: Config) -> int:
    """Parse OPP-115 HTML policies into Chunk records and write chunks.jsonl.

    Returns the number of chunks written.
    """
    raw = Path(raw_dir)
    policy_dir = raw / "sanitized_policies"
    annotation_dir = raw / "annotations"
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    html_files = sorted(policy_dir.glob("*.html"))
    total = 0

    with open(out, "w", encoding="utf-8") as fout:
        for html_path in html_files:
            stem = html_path.stem  # e.g. "105_amazon.com"
            annotation_path = annotation_dir / (stem + ".csv")

            # policy_id: replace dots and hyphens with underscores
            policy_id = re.sub(r"[.\-]", "_", stem)
            # policy_name: strip leading "NNN_" numeric prefix
            policy_name = re.sub(r"^\d+_", "", stem)

            segments = _parse_policy(html_path, annotation_path)
            chunks = _merge_and_chunk(segments, policy_id, policy_name)

            for chunk in chunks:
                fout.write(json.dumps(chunk) + "\n")
                total += 1

    return total


def iter_chunks(chunks_path: str):
    """Yield Chunk dicts from a chunks.jsonl file."""
    with open(chunks_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
