"""Config v2 (CONTRACTS §6): new fields exist and defaults keep the v1 demo working."""
from src.policylens.config import Config


def test_v1_defaults_unchanged():
    cfg = Config()
    assert cfg.embed_model == "BAAI/bge-small-en-v1.5"
    assert cfg.gen_backend == "anthropic"
    assert cfg.top_k == 5
    assert cfg.score_floor == 0.30
    assert cfg.index_dir == "data/index"


def test_v2_defaults_require_zero_new_env_vars():
    cfg = Config()
    assert cfg.retrieval_backend == "chroma"      # pgvector is opt-in until baseline_v1 exists
    assert cfg.db_url_env == "SUPABASE_DB_URL"    # the env var *name*, never a DSN
    assert cfg.hybrid_rrf_k == 60
    assert cfg.fts_candidates == 20
    assert cfg.rerank_enabled is True
    assert cfg.rerank_model == "BAAI/bge-reranker-base"
    assert cfg.rerank_top_n == cfg.top_k
    assert cfg.judge_model == "claude-opus-4-8"
    assert cfg.langfuse_enabled is True
