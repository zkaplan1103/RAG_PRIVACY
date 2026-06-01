"""Shared configuration dataclass — see docs/CONTRACTS.md §4."""
from dataclasses import dataclass, field


@dataclass
class Config:
    embed_backend: str = "local"                   # "local" | "openai"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    gen_backend: str = "anthropic"                 # "anthropic" | "openai"
    gen_model: str = "claude-haiku-4-5"            # swap to sonnet for final cut
    top_k: int = 5
    index_dir: str = "data/index"
    score_floor: float = 0.30                      # below this for all hits => abstain
    processed_dir: str = "data/processed"
    raw_dir: str = "data/raw"


DEFAULT_CONFIG = Config()
