"""Unit tests for ingest.py — run against fixture, not full chunks.jsonl."""
import json
from pathlib import Path

FIXTURE = Path("tests/fixtures/chunks_sample.jsonl")
REQUIRED_KEYS = {"chunk_id", "policy_id", "policy_name", "section", "text", "char_start", "char_end", "source_url"}


def _load_fixture():
    return [json.loads(l) for l in FIXTURE.read_text().splitlines() if l.strip()]


def test_fixture_exists():
    assert FIXTURE.exists()


def test_all_required_keys():
    for chunk in _load_fixture():
        missing = REQUIRED_KEYS - set(chunk.keys())
        assert not missing, f"{chunk['chunk_id']} missing keys: {missing}"


def test_chunk_ids_unique():
    ids = [c["chunk_id"] for c in _load_fixture()]
    assert len(ids) == len(set(ids))


def test_char_offsets():
    for chunk in _load_fixture():
        assert chunk["char_start"] >= 0, f"{chunk['chunk_id']} negative char_start"
        assert chunk["char_end"] > chunk["char_start"], f"{chunk['chunk_id']} char_end <= char_start"


def test_no_html_in_text():
    for chunk in _load_fixture():
        assert "<" not in chunk["text"], f"{chunk['chunk_id']} has HTML in text"


def test_text_nonempty():
    for chunk in _load_fixture():
        assert chunk["text"].strip(), f"{chunk['chunk_id']} has empty text"


def test_iter_chunks():
    from src.policylens.ingest import iter_chunks
    chunks = list(iter_chunks(str(FIXTURE)))
    assert len(chunks) > 0
    for c in chunks:
        assert "chunk_id" in c


def test_policy_ids_are_strings():
    for chunk in _load_fixture():
        assert isinstance(chunk["policy_id"], str)
        assert len(chunk["policy_id"]) > 0
