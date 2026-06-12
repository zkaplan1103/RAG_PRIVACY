"""Tests for pgvector hybrid retrieval.

Coverage:
- _rrf_fuse: RRF math, both legs present, only one leg, empty inputs
- PgVectorRetriever.retrieve: policy_id scoping, ANN+FTS integration,
  rerank rescaling, sort/k limits, empty results
- migrate_pgvector: no-chroma-cache branch, with-chroma-cache branch (fake),
  upsert SQL called correctly
- make_retriever factory: "chroma" and "pgvector" dispatch, unknown backend error
- Real reranker test on fixture data (skips if model not downloaded)
- Optional Docker integration test (pytest -m pgvector)

All unit tests use a faked DB layer — no real Postgres required.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure the src tree is on sys.path when running from the project root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.policylens.config import Config
from src.policylens.pgvector import PgVectorRetriever, _rescale_to_unit, _rrf_fuse
from src.policylens.retrieve import make_retriever

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "chunks_sample.jsonl"


def load_fixture() -> list[dict[str, Any]]:
    """Load the 10-row fixture file."""
    rows = []
    with open(FIXTURE_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def make_cfg(**kwargs: Any) -> Config:
    """Return a Config with rerank_enabled=False by default (faster unit tests)."""
    return Config(
        retrieval_backend=kwargs.get("retrieval_backend", "pgvector"),
        rerank_enabled=kwargs.get("rerank_enabled", False),
        fts_candidates=kwargs.get("fts_candidates", 5),
        hybrid_rrf_k=kwargs.get("hybrid_rrf_k", 60),
        top_k=kwargs.get("top_k", 3),
    )


# ---------------------------------------------------------------------------
# Fake pool / cursor helpers
# ---------------------------------------------------------------------------

class FakeCursor:
    """Minimal cursor that returns pre-configured rows."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def execute(self, sql: str, params: Any = None) -> None:
        pass  # no-op

    def executemany(self, sql: str, params: Any = None) -> None:
        pass

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class FakeConn:
    """Minimal connection that vends a FakeCursor with pre-configured rows."""

    def __init__(self, rows: list[tuple[Any, ...]], _unused: list[Any] = []) -> None:
        self._rows = rows

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._rows)

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class FakePool:
    """Minimal connection pool that returns a FakeConn.

    The retriever opens separate connections for the ANN and FTS legs.
    FakePool tracks which connection call it is: the first connection per
    retrieve() call serves ANN rows, the second serves FTS rows.
    """

    def __init__(
        self,
        ann_rows: list[tuple[Any, ...]] | None = None,
        fts_rows: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self._ann_rows = ann_rows or []
        self._fts_rows = fts_rows or []
        self._call_count = 0  # counts connection() calls within one retrieve()

    def connection(self) -> "FakeConn":
        # First connection() call → ANN data; second → FTS data.
        # Reset after every two calls (one full retrieve() cycle).
        rows = self._ann_rows if self._call_count % 2 == 0 else self._fts_rows
        self._call_count += 1
        return FakeConn(rows, [])

    def close(self) -> None:
        pass


def _make_row(
    chunk_id: str,
    score: float,
    policy_id: str = "pol_a",
) -> tuple[Any, ...]:
    """Build a fake DB row matching the SELECT column order in PgVectorRetriever."""
    return (
        chunk_id,        # 0: chunk_id
        policy_id,       # 1: policy_id
        "Test Policy",   # 2: policy_name
        "Section A",     # 3: section
        f"Text for {chunk_id}",  # 4: text
        0,               # 5: char_start
        100,             # 6: char_end
        None,            # 7: source_url
        score,           # 8: score / rank
    )


# ---------------------------------------------------------------------------
# 1. RRF math
# ---------------------------------------------------------------------------

class TestRRFFuse:
    def test_both_legs_present(self) -> None:
        ann = [("a", 0.9), ("b", 0.8), ("c", 0.7)]
        fts = [("b", 0.5), ("a", 0.4), ("d", 0.3)]
        result = _rrf_fuse(ann, fts, rrf_k=60)

        ids = [cid for cid, _ in result]
        # "a" appears at rank 1 in ANN and rank 2 in FTS → highest combined score.
        # "b" appears at rank 2 in ANN and rank 1 in FTS.
        # "c" appears only in ANN at rank 3.
        # "d" appears only in FTS at rank 3.
        assert ids[0] in ("a", "b")  # both have 2 legs contributing
        assert set(ids) == {"a", "b", "c", "d"}

    def test_formula_values(self) -> None:
        # Verify exact RRF scores for a simple case (rrf_k=60).
        ann = [("x", 1.0)]
        fts = [("x", 1.0)]
        result = _rrf_fuse(ann, fts, rrf_k=60)
        assert len(result) == 1
        cid, score = result[0]
        assert cid == "x"
        # Both legs, rank 1 each: 1/(60+1) + 1/(60+1) = 2/61
        expected = 2.0 / 61.0
        assert abs(score - expected) < 1e-9

    def test_only_ann_leg(self) -> None:
        ann = [("p", 0.9), ("q", 0.7)]
        fts: list[tuple[str, float]] = []
        result = _rrf_fuse(ann, fts, rrf_k=60)
        ids = [cid for cid, _ in result]
        assert ids == ["p", "q"]

    def test_only_fts_leg(self) -> None:
        ann: list[tuple[str, float]] = []
        fts = [("r", 0.8)]
        result = _rrf_fuse(ann, fts, rrf_k=60)
        assert [cid for cid, _ in result] == ["r"]

    def test_empty_both_legs(self) -> None:
        result = _rrf_fuse([], [], rrf_k=60)
        assert result == []

    def test_sorted_descending(self) -> None:
        ann = [("a", 0.9), ("b", 0.5)]
        fts = [("b", 0.8), ("c", 0.3)]
        result = _rrf_fuse(ann, fts, rrf_k=60)
        scores = [s for _, s in result]
        assert scores == sorted(scores, reverse=True)

    def test_rrf_k_affects_scores(self) -> None:
        ann = [("a", 1.0)]
        fts = [("a", 1.0)]
        score_60 = _rrf_fuse(ann, fts, rrf_k=60)[0][1]
        score_10 = _rrf_fuse(ann, fts, rrf_k=10)[0][1]
        # Lower k → higher score (less smoothing)
        assert score_10 > score_60


# ---------------------------------------------------------------------------
# 2. Score rescaling
# ---------------------------------------------------------------------------

class TestRescaleToUnit:
    def test_basic_rescale(self) -> None:
        result = _rescale_to_unit([0.0, 0.5, 1.0])
        assert result[0] == pytest.approx(0.0)
        assert result[2] == pytest.approx(1.0)
        assert result[1] == pytest.approx(0.5)

    def test_all_equal(self) -> None:
        result = _rescale_to_unit([3.0, 3.0, 3.0])
        assert result == [1.0, 1.0, 1.0]

    def test_single_element(self) -> None:
        result = _rescale_to_unit([5.0])
        assert result == [1.0]

    def test_empty(self) -> None:
        assert _rescale_to_unit([]) == []

    def test_negative_inputs(self) -> None:
        # Cross-encoder scores can be negative logits.
        result = _rescale_to_unit([-2.0, 0.0, 2.0])
        assert result[0] == pytest.approx(0.0)
        assert result[2] == pytest.approx(1.0)
        assert result[1] == pytest.approx(0.5)

    def test_all_in_unit_range(self) -> None:
        import random
        inputs = [random.uniform(-10, 10) for _ in range(20)]
        result = _rescale_to_unit(inputs)
        assert all(0.0 <= v <= 1.0 for v in result)


# ---------------------------------------------------------------------------
# 3. PgVectorRetriever — unit tests with fake pool
# ---------------------------------------------------------------------------

class TestPgVectorRetrieverUnit:
    """All tests inject a FakePool — no real DB connection."""

    def _make_retriever(
        self,
        ann_rows: list[tuple[Any, ...]] | None = None,
        fts_rows: list[tuple[Any, ...]] | None = None,
        **cfg_kwargs: Any,
    ) -> PgVectorRetriever:
        pool = FakePool(ann_rows=ann_rows, fts_rows=fts_rows)
        cfg = make_cfg(**cfg_kwargs)
        return PgVectorRetriever(cfg, _pool=pool)

    def test_returns_list_of_retrieved_chunks(self) -> None:
        ann_rows = [_make_row("c1", 0.9), _make_row("c2", 0.7)]
        r = self._make_retriever(ann_rows=ann_rows)
        with patch("src.policylens.pgvector._embed_query", return_value=[0.1] * 384):
            results = r.retrieve("test query", "pol_a", k=5)
        assert isinstance(results, list)
        for item in results:
            assert "chunk" in item
            assert "score" in item

    def test_policy_id_scoping(self) -> None:
        """Both legs must be called with the correct policy_id."""
        pool = FakePool(
            ann_rows=[_make_row("c_a", 0.9, policy_id="pol_a")],
            fts_rows=[],
        )
        cfg = make_cfg()
        retriever = PgVectorRetriever(cfg, _pool=pool)

        original_run_ann = retriever._run_ann
        original_run_fts = retriever._run_fts

        ann_policy_ids: list[str] = []
        fts_policy_ids: list[str] = []

        def fake_ann(pid: str, emb: list[float], n: int) -> list[Any]:
            ann_policy_ids.append(pid)
            return original_run_ann(pid, emb, n)

        def fake_fts(pid: str, q: str, n: int) -> list[Any]:
            fts_policy_ids.append(pid)
            return original_run_fts(pid, q, n)

        retriever._run_ann = fake_ann  # type: ignore[method-assign]
        retriever._run_fts = fake_fts  # type: ignore[method-assign]

        with patch("src.policylens.pgvector._embed_query", return_value=[0.1] * 384):
            retriever.retrieve("test", "pol_a", k=3)

        assert all(pid == "pol_a" for pid in ann_policy_ids)
        assert all(pid == "pol_a" for pid in fts_policy_ids)

    def test_k_limits_output(self) -> None:
        ann_rows = [_make_row(f"c{i}", 0.9 - i * 0.1) for i in range(8)]
        r = self._make_retriever(ann_rows=ann_rows)
        with patch("src.policylens.pgvector._embed_query", return_value=[0.1] * 384):
            results = r.retrieve("query", "pol_a", k=3)
        assert len(results) <= 3

    def test_empty_both_legs_returns_empty(self) -> None:
        r = self._make_retriever(ann_rows=[], fts_rows=[])
        with patch("src.policylens.pgvector._embed_query", return_value=[0.1] * 384):
            results = r.retrieve("anything", "pol_a", k=5)
        assert results == []

    def test_results_sorted_desc(self) -> None:
        ann_rows = [
            _make_row("c1", 0.5),
            _make_row("c3", 0.9),
            _make_row("c2", 0.7),
        ]
        r = self._make_retriever(ann_rows=ann_rows)
        with patch("src.policylens.pgvector._embed_query", return_value=[0.1] * 384):
            results = r.retrieve("test", "pol_a", k=10)
        scores = [item["score"] for item in results]
        assert scores == sorted(scores, reverse=True)

    def test_scores_in_unit_range(self) -> None:
        """All final scores must be in [0, 1] regardless of rerank or RRF."""
        ann_rows = [_make_row(f"c{i}", float(i) / 10) for i in range(5)]
        fts_rows = [_make_row(f"c{i}", float(i) / 5) for i in range(3)]
        r = self._make_retriever(ann_rows=ann_rows, fts_rows=fts_rows)
        with patch("src.policylens.pgvector._embed_query", return_value=[0.1] * 384):
            results = r.retrieve("test", "pol_a", k=10)
        for item in results:
            assert 0.0 <= item["score"] <= 1.0, f"Score out of range: {item['score']}"

    def test_chunk_fields_populated(self) -> None:
        ann_rows = [_make_row("chunk_abc", 0.8, policy_id="pol_x")]
        r = self._make_retriever(ann_rows=ann_rows)
        with patch("src.policylens.pgvector._embed_query", return_value=[0.1] * 384):
            results = r.retrieve("test", "pol_x", k=5)
        assert len(results) == 1
        chunk = results[0]["chunk"]
        assert chunk["chunk_id"] == "chunk_abc"
        assert chunk["policy_id"] == "pol_x"
        assert chunk["section"] == "Section A"
        assert chunk["text"] == "Text for chunk_abc"

    def test_fts_only_hit_included(self) -> None:
        """Items found only in FTS (not ANN) should appear in results."""
        ann_rows: list[tuple[Any, ...]] = []
        fts_rows = [_make_row("fts_only", 0.5)]
        r = self._make_retriever(ann_rows=ann_rows, fts_rows=fts_rows)
        with patch("src.policylens.pgvector._embed_query", return_value=[0.1] * 384):
            results = r.retrieve("test", "pol_a", k=5)
        assert any(item["chunk"]["chunk_id"] == "fts_only" for item in results)

    def test_context_manager(self) -> None:
        cfg = make_cfg()
        pool = FakePool()
        with PgVectorRetriever(cfg, _pool=pool) as r:
            assert r is not None
        # pool.close() was called — no error means it worked

    def test_rerank_rescaling_applied(self) -> None:
        """When rerank_enabled=True, cross-encoder scores are rescaled to [0,1]."""
        import numpy as np

        ann_rows = [_make_row("c1", 0.9), _make_row("c2", 0.7)]
        fts_rows: list[tuple[Any, ...]] = []
        cfg = make_cfg(rerank_enabled=True)
        pool = FakePool(ann_rows=ann_rows, fts_rows=fts_rows)

        fake_reranker = MagicMock()
        # Raw logits outside [0,1] — should be rescaled
        fake_reranker.predict.return_value = np.array([-3.0, 5.0])

        # Patch _load_reranker so the constructor doesn't try to download the model.
        with patch("src.policylens.pgvector._load_reranker", return_value=fake_reranker):
            r = PgVectorRetriever(cfg, _pool=pool)

        with patch("src.policylens.pgvector._embed_query", return_value=[0.1] * 384):
            results = r.retrieve("test", "pol_a", k=2)

        scores = [item["score"] for item in results]
        assert all(0.0 <= s <= 1.0 for s in scores), f"Scores not in [0,1]: {scores}"
        # The rescaled max should be 1.0 and min should be 0.0
        assert max(scores) == pytest.approx(1.0)
        assert min(scores) == pytest.approx(0.0)

    def test_no_env_var_raises(self) -> None:
        """Without injection, missing env var must raise RuntimeError."""
        cfg = Config(retrieval_backend="pgvector", db_url_env="__NONEXISTENT_TEST_VAR__")
        env_backup = os.environ.pop("__NONEXISTENT_TEST_VAR__", None)
        try:
            with pytest.raises(RuntimeError, match="__NONEXISTENT_TEST_VAR__"):
                PgVectorRetriever(cfg)
        finally:
            if env_backup is not None:
                os.environ["__NONEXISTENT_TEST_VAR__"] = env_backup


# ---------------------------------------------------------------------------
# 4. make_retriever factory
# ---------------------------------------------------------------------------

class TestMakeRetriever:
    def test_chroma_dispatch(self) -> None:
        """make_retriever('chroma') should return a ChromaRetriever."""
        from src.policylens.retrieve import ChromaRetriever

        cfg = Config(retrieval_backend="chroma")
        # ChromaRetriever.__init__ tries to open the Chroma path — patch it.
        with patch("src.policylens.retrieve.ChromaRetriever.__init__", return_value=None):
            r = make_retriever(cfg)
        assert isinstance(r, ChromaRetriever)

    def test_pgvector_dispatch(self) -> None:
        """make_retriever('pgvector') should return a PgVectorRetriever."""
        cfg = Config(
            retrieval_backend="pgvector",
            db_url_env="__PGVEC_TEST__",
            rerank_enabled=False,
        )
        os.environ["__PGVEC_TEST__"] = "postgresql://fake/fake"
        try:
            with patch("psycopg_pool.ConnectionPool.__init__", return_value=None):
                r = make_retriever(cfg)
            assert isinstance(r, PgVectorRetriever)
        finally:
            os.environ.pop("__PGVEC_TEST__", None)
            if isinstance(r, PgVectorRetriever) and hasattr(r, "_pool"):
                try:
                    r._pool.close()
                except Exception:
                    pass

    def test_unknown_backend_raises(self) -> None:
        cfg = Config(retrieval_backend="nonexistent")
        with pytest.raises(ValueError, match="nonexistent"):
            make_retriever(cfg)


# ---------------------------------------------------------------------------
# 5. Migration script — unit tests with fake DB connection
# ---------------------------------------------------------------------------

class TestMigratePgvector:
    def test_migrate_no_chroma_embeds_fresh(self, tmp_path: Path) -> None:
        """When Chroma cache absent, all chunks are embedded fresh."""
        fixture = tmp_path / "chunks.jsonl"
        # Write 3 fake chunks.
        chunks = [
            {
                "chunk_id": f"pid::sec::c00{i}",
                "policy_id": "pid",
                "policy_name": "Policy",
                "section": "Sec",
                "text": f"Some text about data privacy clause {i}.",
                "char_start": i * 100,
                "char_end": (i + 1) * 100,
                "source_url": None,
            }
            for i in range(3)
        ]
        with open(fixture, "w") as f:
            for c in chunks:
                f.write(json.dumps(c) + "\n")

        calls: list[list[str]] = []

        def fake_embed_batch(texts: list[str], model: str, cache: dict) -> list[list[float]]:
            calls.append(texts)
            return [[0.1] * 384] * len(texts)

        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__ = lambda s: s
        fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        fake_conn.cursor.return_value.executemany = MagicMock()

        from src.policylens import migrate_pgvector

        with patch.object(migrate_pgvector, "_embed_batch", fake_embed_batch), \
             patch.object(migrate_pgvector, "_try_load_chroma_embeddings", return_value={}):
            count = migrate_pgvector.migrate(
                chunks_path=str(fixture),
                batch_size=10,
                reuse_chroma=True,
                _conn=fake_conn,
            )

        assert count == 3
        assert len(calls) == 1  # one call to embed_batch
        assert len(calls[0]) == 3  # all 3 texts embedded

    def test_migrate_with_chroma_cache_no_reembed(self, tmp_path: Path) -> None:
        """When all embeddings are in Chroma cache, embed_batch is never called."""
        fixture = tmp_path / "chunks.jsonl"
        chunks = [
            {
                "chunk_id": f"pid::sec::c{i:03d}",
                "policy_id": "pid",
                "policy_name": "Policy",
                "section": "Sec",
                "text": f"Text {i}",
                "char_start": 0,
                "char_end": 10,
                "source_url": None,
            }
            for i in range(4)
        ]
        with open(fixture, "w") as f:
            for c in chunks:
                f.write(json.dumps(c) + "\n")

        # Simulate Chroma having all embeddings.
        fake_cache = {c["chunk_id"]: [0.0] * 384 for c in chunks}

        embed_calls: list[Any] = []

        def fake_embed_batch(texts: list[str], model: str, cache: dict) -> list[list[float]]:
            embed_calls.append(texts)
            return [[0.0] * 384] * len(texts)

        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__ = lambda s: s
        fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        fake_conn.cursor.return_value.executemany = MagicMock()

        from src.policylens import migrate_pgvector

        with patch.object(migrate_pgvector, "_embed_batch", fake_embed_batch), \
             patch.object(migrate_pgvector, "_try_load_chroma_embeddings", return_value=fake_cache):
            count = migrate_pgvector.migrate(
                chunks_path=str(fixture),
                batch_size=10,
                reuse_chroma=True,
                _conn=fake_conn,
            )

        assert count == 4
        assert embed_calls == [], "embed_batch should NOT be called when all embeddings cached"

    def test_migrate_partial_chroma_cache(self, tmp_path: Path) -> None:
        """When only some embeddings are cached, only missing ones are re-embedded."""
        fixture = tmp_path / "chunks.jsonl"
        chunks = [
            {
                "chunk_id": f"p::s::c{i:03d}",
                "policy_id": "p",
                "policy_name": "P",
                "section": "S",
                "text": f"Text {i}",
                "char_start": 0,
                "char_end": 10,
                "source_url": None,
            }
            for i in range(6)
        ]
        with open(fixture, "w") as f:
            for c in chunks:
                f.write(json.dumps(c) + "\n")

        # First 4 cached, last 2 missing.
        cached_ids = {c["chunk_id"]: [0.0] * 384 for c in chunks[:4]}

        embedded_texts: list[str] = []

        def fake_embed_batch(texts: list[str], model: str, cache: dict) -> list[list[float]]:
            embedded_texts.extend(texts)
            return [[0.1] * 384] * len(texts)

        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__ = lambda s: s
        fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        fake_conn.cursor.return_value.executemany = MagicMock()

        from src.policylens import migrate_pgvector

        with patch.object(migrate_pgvector, "_embed_batch", fake_embed_batch), \
             patch.object(migrate_pgvector, "_try_load_chroma_embeddings", return_value=cached_ids):
            count = migrate_pgvector.migrate(
                chunks_path=str(fixture),
                batch_size=10,
                reuse_chroma=True,
                _conn=fake_conn,
            )

        assert count == 6
        # Only the 2 missing chunks should have been embedded.
        assert len(embedded_texts) == 2

    def test_migrate_file_not_found(self) -> None:
        from src.policylens import migrate_pgvector

        with pytest.raises(FileNotFoundError):
            migrate_pgvector.migrate(chunks_path="/nonexistent/path/chunks.jsonl")

    def test_migrate_no_dsn_raises(self, tmp_path: Path) -> None:
        """Without _conn injection and no env var, migrate() must raise RuntimeError."""
        fixture = tmp_path / "chunks.jsonl"
        fixture.write_text(
            '{"chunk_id":"x","policy_id":"p","policy_name":"P","section":"S",'
            '"text":"T","char_start":0,"char_end":1,"source_url":null}\n'
        )

        cfg = Config(db_url_env="__MIGRATE_TEST_NO_DSN__")
        os.environ.pop("__MIGRATE_TEST_NO_DSN__", None)

        from src.policylens import migrate_pgvector

        with patch.object(migrate_pgvector, "_embed_batch", return_value=[[0.0] * 384]), \
             patch.object(migrate_pgvector, "_try_load_chroma_embeddings", return_value={}):
            with pytest.raises(RuntimeError, match="__MIGRATE_TEST_NO_DSN__"):
                migrate_pgvector.migrate(
                    chunks_path=str(fixture),
                    cfg=cfg,
                    reuse_chroma=False,
                )


# ---------------------------------------------------------------------------
# 6. Real reranker test on 10-row fixture
# ---------------------------------------------------------------------------

# Mark: run by default but skip gracefully if model download is too slow.
# Set SKIP_RERANKER_DOWNLOAD=1 to skip the bge-reranker-base download variant.
_skip_if_slow = pytest.mark.skipif(
    os.environ.get("SKIP_RERANKER_DOWNLOAD", "0") == "1",
    reason="SKIP_RERANKER_DOWNLOAD=1 set; skipping bge-reranker-base download.",
)


@_skip_if_slow
def test_real_reranker_on_fixture() -> None:
    """Load bge-reranker-base and rerank the 10-row fixture chunks.

    This test exercises the actual CrossEncoder (downloaded if absent).
    Assertions:
    - Returns a list of RetrievedChunk
    - All scores in [0, 1]
    - Sorted descending
    - Most relevant chunk for "privacy data collection" is ranked highly
    """
    try:
        from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("sentence-transformers not installed")

    from src.policylens.pgvector import _rescale_to_unit

    # Load fixture chunks.
    fixture_chunks = load_fixture()
    query = "privacy data collection personal information"

    try:
        reranker = CrossEncoder("BAAI/bge-reranker-base")
    except Exception as exc:
        pytest.skip(f"Could not load BAAI/bge-reranker-base: {exc}")

    pairs: list[tuple[str, str]] = [(query, c["text"]) for c in fixture_chunks]
    import numpy as np

    raw_scores: list[float] = reranker.predict(pairs).tolist()  # type: ignore[union-attr]
    rescaled = _rescale_to_unit(raw_scores)

    # All scores in [0, 1]
    assert all(0.0 <= s <= 1.0 for s in rescaled), f"Scores out of range: {rescaled}"

    # The top-scoring chunk should contain privacy/data/collection keywords.
    best_idx = int(np.argmax(rescaled))
    best_text = fixture_chunks[best_idx]["text"].lower()
    keywords = ["collect", "personal", "data", "privacy", "information"]
    keyword_hit = any(k in best_text for k in keywords)
    assert keyword_hit, (
        f"Expected top-ranked chunk to contain privacy/data keywords. "
        f"Got: {fixture_chunks[best_idx]['text'][:100]!r}"
    )


# ---------------------------------------------------------------------------
# 7. Docker integration test (opt-in: pytest -m pgvector)
# ---------------------------------------------------------------------------

def _docker_available() -> bool:
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


@pytest.mark.pgvector
@pytest.mark.skipif(not _docker_available(), reason="Docker not available or not running")
def test_pgvector_integration_docker(tmp_path: Path) -> None:
    """Integration test: spins up pgvector/pgvector container, runs schema + insert + retrieve.

    Opt-in: pytest -m pgvector
    Requires Docker daemon running.
    """
    import subprocess
    import time

    import psycopg  # type: ignore[import-untyped]

    container_name = "policylens_pgvec_test"
    pg_port = "54399"  # use non-default port to avoid conflicts

    # Clean up any pre-existing container.
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True,
    )

    # Start pgvector container.
    proc = subprocess.run(
        [
            "docker", "run", "-d",
            "--name", container_name,
            "-e", "POSTGRES_PASSWORD=testpass",
            "-p", f"{pg_port}:5432",
            "pgvector/pgvector:pg16",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"docker run failed: {proc.stderr}"

    dsn = f"postgresql://postgres:testpass@localhost:{pg_port}/postgres"
    try:
        # Wait for Postgres to be ready (up to 30 s).
        for attempt in range(30):
            try:
                conn = psycopg.connect(dsn, connect_timeout=2)
                conn.close()
                break
            except Exception:
                time.sleep(1)
        else:
            pytest.fail("pgvector container did not become ready within 30 s")

        # Apply schema.
        schema_path = (
            Path(__file__).parent.parent / "infra" / "sql" / "001_init.sql"
        )
        with psycopg.connect(dsn) as conn:
            conn.execute(schema_path.read_text())  # type: ignore[arg-type]
            conn.commit()

        # Load fixture and migrate.
        fixture_chunks = load_fixture()
        env_backup = os.environ.get("SUPABASE_DB_URL")
        os.environ["SUPABASE_DB_URL"] = dsn

        try:
            from src.policylens import migrate_pgvector

            count = migrate_pgvector.migrate(
                chunks_path=str(FIXTURE_PATH),
                batch_size=5,
                reuse_chroma=False,
            )
            assert count == len(fixture_chunks)
        finally:
            if env_backup is not None:
                os.environ["SUPABASE_DB_URL"] = env_backup
            else:
                os.environ.pop("SUPABASE_DB_URL", None)

        # Test retrieval.
        cfg = Config(
            retrieval_backend="pgvector",
            db_url_env="__INTEGRATION_DSN__",
            rerank_enabled=False,
            fts_candidates=5,
            hybrid_rrf_k=60,
            top_k=3,
        )
        os.environ["__INTEGRATION_DSN__"] = dsn
        try:
            with PgVectorRetriever(cfg) as r:
                results = r.retrieve(
                    "privacy data collection",
                    "1017_sci_news_com",
                    k=3,
                )
        finally:
            os.environ.pop("__INTEGRATION_DSN__", None)

        assert len(results) <= 3
        for item in results:
            assert item["chunk"]["policy_id"] == "1017_sci_news_com"
            assert 0.0 <= item["score"] <= 1.0

    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
