"""Unit tests for index + retrieval — build from fixture, not full chunks.jsonl."""
import json
import pytest
from pathlib import Path

FIXTURE = "tests/fixtures/chunks_sample.jsonl"


def _fixture_policy_ids():
    with open(FIXTURE) as f:
        return list(set(json.loads(l)["policy_id"] for l in f if l.strip()))


@pytest.fixture(scope="module")
def tmp_index(tmp_path_factory):
    from src.policylens.index import build_index
    from src.policylens.config import Config

    idx_dir = str(tmp_path_factory.mktemp("chroma_index"))
    cfg = Config(index_dir=idx_dir)
    build_index(FIXTURE, cfg)
    return cfg


def test_index_builds(tmp_index):
    import chromadb
    client = chromadb.PersistentClient(path=tmp_index.index_dir)
    col = client.get_collection("policylens")
    assert col.count() == 10


def test_retrieval_returns_results(tmp_index):
    from src.policylens.retrieve import ChromaRetriever
    policy_id = _fixture_policy_ids()[0]
    r = ChromaRetriever(tmp_index)
    hits = r.retrieve("collect personal information", policy_id, k=3)
    assert len(hits) > 0


def test_retrieval_sorted_by_score(tmp_index):
    from src.policylens.retrieve import ChromaRetriever
    policy_id = _fixture_policy_ids()[0]
    r = ChromaRetriever(tmp_index)
    hits = r.retrieve("privacy data collection", policy_id, k=5)
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_retrieval_scoped_to_policy(tmp_index):
    from src.policylens.retrieve import ChromaRetriever
    policy_id = _fixture_policy_ids()[0]
    r = ChromaRetriever(tmp_index)
    hits = r.retrieve("privacy", policy_id, k=5)
    for h in hits:
        assert h["chunk"]["policy_id"] == policy_id


def test_retrieval_chunk_schema(tmp_index):
    from src.policylens.retrieve import ChromaRetriever
    policy_id = _fixture_policy_ids()[0]
    r = ChromaRetriever(tmp_index)
    hits = r.retrieve("data sharing", policy_id, k=3)
    assert len(hits) > 0
    for h in hits:
        assert isinstance(h["score"], float)
        chunk = h["chunk"]
        for key in ("chunk_id", "policy_id", "policy_name", "section", "text", "char_start", "char_end"):
            assert key in chunk, f"missing key: {key}"


def test_no_results_wrong_policy(tmp_index):
    from src.policylens.retrieve import ChromaRetriever
    r = ChromaRetriever(tmp_index)
    hits = r.retrieve("data sharing", "nonexistent_policy_xyz_123", k=5)
    assert hits == []


def test_cache_skips_reindex(tmp_index):
    """Second build_index call on same dir should skip."""
    from src.policylens.index import build_index
    import chromadb
    build_index(FIXTURE, tmp_index)
    client = chromadb.PersistentClient(path=tmp_index.index_dir)
    col = client.get_collection("policylens")
    assert col.count() == 10


def test_fixture_retriever_scoring():
    from src.policylens.retrieve import FixtureRetriever
    policy_id = _fixture_policy_ids()[0]
    r = FixtureRetriever(FIXTURE)
    hits = r.retrieve("personal information collection", policy_id, k=3)
    assert len(hits) <= 3
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)
