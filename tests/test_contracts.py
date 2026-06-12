"""Verify that the frozen contract interfaces are importable and fixtures are valid."""
import json
from pathlib import Path

FIXTURE_PATH = Path("tests/fixtures/chunks_sample.jsonl")
REQUIRED_CHUNK_KEYS = {
    "chunk_id", "policy_id", "policy_name", "section",
    "text", "char_start", "char_end", "source_url",
}


def test_fixture_exists():
    assert FIXTURE_PATH.exists(), "chunks_sample.jsonl fixture is missing"


def test_fixture_has_ten_rows():
    rows = [json.loads(line) for line in FIXTURE_PATH.read_text().splitlines() if line.strip()]
    assert len(rows) == 10, f"Expected 10 fixture rows, got {len(rows)}"


def test_fixture_chunk_schema():
    rows = [json.loads(line) for line in FIXTURE_PATH.read_text().splitlines() if line.strip()]
    for row in rows:
        missing = REQUIRED_CHUNK_KEYS - set(row.keys())
        assert not missing, f"Chunk {row.get('chunk_id')} missing keys: {missing}"


def test_fixture_chunk_ids_unique():
    rows = [json.loads(line) for line in FIXTURE_PATH.read_text().splitlines() if line.strip()]
    ids = [r["chunk_id"] for r in rows]
    assert len(ids) == len(set(ids)), "Duplicate chunk_ids in fixture"


def test_config_importable():
    from src.policylens.config import DEFAULT_CONFIG, Config  # noqa: F401
    cfg = Config()
    assert cfg.embed_model == "BAAI/bge-small-en-v1.5"
    assert cfg.score_floor == 0.30


def test_generate_schema_importable():
    from src.policylens.generate import Answer, Citation, canned_answer  # noqa: F401
    a = canned_answer()
    assert a["answerable"] is True
    assert len(a["citations"]) >= 1
    assert all(k in a["citations"][0] for k in ("chunk_id", "section", "quote"))


def test_fixture_retriever():
    from src.policylens.retrieve import FixtureRetriever
    r = FixtureRetriever("tests/fixtures/chunks_sample.jsonl")
    results = r.retrieve("share data advertising", "fixture_policy", k=3)
    assert len(results) <= 3
    for hit in results:
        assert "chunk" in hit and "score" in hit
        assert hit["chunk"]["policy_id"] == "fixture_policy"
