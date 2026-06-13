"""RED-TEAM PoC: SQL injection via adversarial policy_id / query in pgvector.

Capture every (sql, params) pair the retriever sends to the cursor and prove
the injection payload always travels as a bound parameter, never concatenated
into the SQL text. Also covers the migrate upsert path.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from policylens.config import Config  # noqa: E402
from policylens.pgvector import PgVectorRetriever  # noqa: E402

INJECTION = "x'; DROP TABLE chunks; --"


class RecordingCursor:
    def __init__(self, calls: list[tuple[str, Any]]) -> None:
        self._calls = calls

    def execute(self, sql: str, params: Any = None) -> None:
        self._calls.append((sql, params))

    def executemany(self, sql: str, params: Any = None) -> None:
        self._calls.append((sql, params))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return []

    def __enter__(self) -> "RecordingCursor":
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class RecordingConn:
    def __init__(self, calls: list[tuple[str, Any]]) -> None:
        self._calls = calls

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self._calls)

    def __enter__(self) -> "RecordingConn":
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class RecordingPool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def connection(self) -> RecordingConn:
        return RecordingConn(self.calls)

    def close(self) -> None:
        pass


def _embed_stub(query: str, model: str):  # noqa: ARG001
    return [0.0] * 384


def test_policy_id_and_query_are_parameterized(monkeypatch):
    import policylens.pgvector as pg
    monkeypatch.setattr(pg, "_embed_query", _embed_stub)

    pool = RecordingPool()
    cfg = Config(retrieval_backend="pgvector", rerank_enabled=False, fts_candidates=5)
    r = PgVectorRetriever(cfg, _pool=pool)

    r.retrieve(INJECTION, INJECTION, k=3)

    assert pool.calls, "no SQL executed"
    for sql, params in pool.calls:
        # The injection string must NOT be interpolated into the SQL text.
        assert INJECTION not in sql, f"injection reached SQL text: {sql!r}"
        # It MUST appear in the bound params instead.
        flat = [p for p in (params or ())]
        assert any(p == INJECTION for p in flat), "policy_id/query not bound as param"
        # SQL uses %s placeholders (psycopg client-side binding)
        assert "%s" in sql


def test_migrate_upsert_parameterized(monkeypatch):
    """Adversarial chunk fields go through executemany params, not SQL text."""
    import policylens.migrate_pgvector as mig

    pool_calls: list[tuple[str, Any]] = []
    conn = RecordingConn(pool_calls)

    chunks = [{
        "chunk_id": INJECTION, "policy_id": INJECTION, "policy_name": INJECTION,
        "section": INJECTION, "text": INJECTION, "char_start": 0, "char_end": 1,
        "source_url": INJECTION,
    }]
    embeddings = {INJECTION: [0.0] * 384}

    mig._do_upsert(conn, chunks, embeddings, batch_size=10)

    assert pool_calls, "no upsert executed"
    for sql, params in pool_calls:
        assert INJECTION not in sql, f"injection reached upsert SQL: {sql!r}"
        # executemany params is a list of row-tuples
        assert any(INJECTION in row for row in params)
