-- PolicyLens pgvector schema — see docs/CONTRACTS.md §7
-- Run against a Supabase / Postgres instance that has the pgvector extension.
-- Script is idempotent (IF NOT EXISTS guards everywhere).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    text PRIMARY KEY,
    policy_id   text NOT NULL,
    policy_name text NOT NULL,
    section     text NOT NULL,
    text        text NOT NULL,
    char_start  int  NOT NULL,
    char_end    int  NOT NULL,
    source_url  text,
    embedding   vector(384) NOT NULL,             -- bge-small-en-v1.5 (384-dim)
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);

-- HNSW index for fast cosine-ANN search
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- GIN index for full-text search (tsvector column)
CREATE INDEX IF NOT EXISTS chunks_tsv_idx
    ON chunks USING gin (tsv);

-- B-tree index for policy_id scoping (WHERE policy_id = $1 filters)
CREATE INDEX IF NOT EXISTS chunks_policy_idx
    ON chunks (policy_id);
