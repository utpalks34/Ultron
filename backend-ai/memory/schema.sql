-- Ultron memory + registry schema. Idempotent: safe to re-run.
-- LangGraph checkpoint tables are NOT created here; the library creates them itself.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS documents (
  id          BIGSERIAL PRIMARY KEY,
  source_path TEXT NOT NULL UNIQUE,
  title       TEXT,
  mime        TEXT,
  sha256      TEXT NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
  id          BIGSERIAL PRIMARY KEY,
  document_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
  ordinal     INT NOT NULL,
  content     TEXT NOT NULL,
  token_count INT,
  embedding   VECTOR(768) NOT NULL,
  collection  TEXT NOT NULL DEFAULT 'personal'
    CHECK (collection IN ('personal','scripture','dialogue')),
  metadata    JSONB NOT NULL DEFAULT '{}',   -- e.g. {"chapter":2,"verse":47}
  UNIQUE (document_id, ordinal)
);

CREATE TABLE IF NOT EXISTS episodes (
  id         BIGSERIAL PRIMARY KEY,
  run_id     TEXT NOT NULL,
  agent      TEXT NOT NULL,
  summary    TEXT NOT NULL,
  embedding  VECTOR(768),
  outcome    TEXT CHECK (outcome IN ('ok','failed','cancelled')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chunks_hnsw ON chunks
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS chunks_trgm ON chunks USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS chunks_collection ON chunks (collection);
CREATE INDEX IF NOT EXISTS episodes_recent ON episodes (created_at DESC);

CREATE TABLE IF NOT EXISTS registry_apps (
  id           BIGSERIAL PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  aliases      TEXT[] NOT NULL DEFAULT '{}',
  launch_cmd   TEXT NOT NULL,
  process_name TEXT,
  protected    BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS registry_media (
  id        BIGSERIAL PRIMARY KEY,
  title     TEXT NOT NULL,
  artist    TEXT,
  path      TEXT NOT NULL UNIQUE,
  tags      TEXT[] NOT NULL DEFAULT '{}',
  embedding VECTOR(768)
);

CREATE TABLE IF NOT EXISTS registry_contacts (
  id      BIGSERIAL PRIMARY KEY,
  name    TEXT NOT NULL UNIQUE,
  aliases TEXT[] NOT NULL DEFAULT '{}',
  phone   TEXT,
  channel TEXT CHECK (channel IN ('whatsapp','sms','email'))
);

-- Phase 5.1 additions (idempotent).
-- The lexical branch of retrieval uses full-text search (chunks_fts);
-- chunks_trgm stays for later fuzzy matching.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS collection TEXT NOT NULL DEFAULT 'personal'
  CHECK (collection IN ('personal','scripture','dialogue'));
CREATE INDEX IF NOT EXISTS documents_collection ON documents (collection);
CREATE INDEX IF NOT EXISTS chunks_meta ON chunks USING gin (metadata jsonb_path_ops);
CREATE INDEX IF NOT EXISTS chunks_fts ON chunks USING gin (to_tsvector('english', content));
