"""Shared configuration dataclass — see docs/CONTRACTS.md §4 (v1) and §6 (v2)."""
from dataclasses import dataclass


@dataclass
class Config:
    # --- v1 (frozen, CONTRACTS §4) ---
    embed_backend: str = "local"                   # "local" | "openai"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    gen_backend: str = "anthropic"                 # "anthropic" | "openai"
    gen_model: str = "claude-haiku-4-5"            # swap to sonnet for final cut
    top_k: int = 5
    index_dir: str = "data/index"
    score_floor: float = 0.30                      # below this for all hits => abstain
    processed_dir: str = "data/processed"
    raw_dir: str = "data/raw"

    # --- v2 (production upgrade, CONTRACTS §6) ---
    # chroma stays the default until the baseline_v1 eval run is saved (see UPGRADE_PLAN)
    retrieval_backend: str = "chroma"              # "chroma" | "pgvector"
    db_url_env: str = "SUPABASE_DB_URL"            # env var NAME with the DSN, never the DSN
    hybrid_rrf_k: int = 60                         # RRF constant fusing vector + FTS ranks
    fts_candidates: int = 20                       # candidates per leg before fusion
    rerank_enabled: bool = True                    # pgvector path only; chroma path ignores
    rerank_model: str = "BAAI/bge-reranker-base"   # local cross-encoder
    rerank_top_n: int = 5                          # final k after rerank
    judge_model: str = "claude-opus-4-8"           # Ragas judge; env override EVAL_JUDGE_MODEL
    langfuse_enabled: bool = True                  # auto-disables if LANGFUSE_* env vars absent


DEFAULT_CONFIG = Config()
