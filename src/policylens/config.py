"""Shared configuration dataclass — see docs/CONTRACTS.md §4 and §6."""
from dataclasses import dataclass


@dataclass
class Config:
    # --- v1 fields (CONTRACTS §4) ---
    embed_backend: str = "local"                   # "local" | "openai"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    gen_backend: str = "anthropic"                 # "anthropic" | "openai"
    gen_model: str = "claude-haiku-4-5"            # swap to sonnet for final cut
    top_k: int = 5
    index_dir: str = "data/index"
    score_floor: float = 0.30                      # below this for all hits => abstain
    processed_dir: str = "data/processed"
    raw_dir: str = "data/raw"
    # --- v2 fields (CONTRACTS §6) — backward-compatible defaults ---
    retrieval_backend: str = "chroma"              # "chroma" | "pgvector"
    db_url_env: str = "SUPABASE_DB_URL"            # env var NAME (never the DSN itself)
    hybrid_rrf_k: int = 60
    fts_candidates: int = 20
    rerank_enabled: bool = True
    rerank_model: str = "BAAI/bge-reranker-base"
    rerank_top_n: int = 5
    judge_model: str = "claude-opus-4-8"
    langfuse_enabled: bool = True                  # auto-disables when LANGFUSE_* vars absent


DEFAULT_CONFIG = Config()
