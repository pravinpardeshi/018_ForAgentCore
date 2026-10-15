-- ServiceNow closed-incidents knowledge base for PostgreSQL + pgvector.
--
-- Your ingestion pipeline owns INSERT/UPDATE. This schema is the contract
-- the Knowledge Agent queries. Adjust EMBEDDING dims to match your
-- BEDROCK_EMBEDDING_MODEL_ID (Titan v2 = 1024, Cohere v3 = 1024).
--
-- Apply:  psql "$DATABASE_URL" -f sql/schema.sql

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 1024 = amazon.titan-embed-text-v2 default. Change if you embed otherwise.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_tables WHERE tablename = 'snow_closed_incidents') THEN
    CREATE TABLE snow_closed_incidents (
      incident_number   TEXT PRIMARY KEY,          -- e.g. INC0012345
      short_description TEXT NOT NULL,
      description       TEXT,
      resolution_notes  TEXT,
      root_cause        TEXT,
      work_notes_summary TEXT,
      category          TEXT,
      subcategory       TEXT,
      assignment_group  TEXT,
      resolved_by       TEXT,
      priority          TEXT,
      state             TEXT NOT NULL DEFAULT 'Closed',
      opened_at         TIMESTAMPTZ,
      resolved_at       TIMESTAMPTZ,
      search_text       TEXT GENERATED ALWAYS AS (
        COALESCE(short_description,'') || E'\n' ||
        COALESCE(description,'') || E'\n' ||
        COALESCE(resolution_notes,'') || E'\n' ||
        COALESCE(root_cause,'')
      ) STORED,
      search_tsv        TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english',
          COALESCE(short_description,'') || ' ' ||
          COALESCE(description,'') || ' ' ||
          COALESCE(resolution_notes,'') || ' ' ||
          COALESCE(root_cause,''))
      ) STORED,
      embedding         vector(1024) NOT NULL,
      metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
      updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    );
  END IF;
END $$;

-- Vector index (HNSW, cosine). Build AFTER bulk load for speed, or concurrently.
CREATE INDEX IF NOT EXISTS idx_snow_inc_embedding
  ON snow_closed_incidents USING hnsw (embedding vector_cosine_ops);

-- Keyword / filter indexes
CREATE INDEX IF NOT EXISTS idx_snow_inc_tsv ON snow_closed_incidents USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS idx_snow_inc_trgm ON snow_closed_incidents USING GIN (short_description gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_snow_inc_cat ON snow_closed_incidents (category);
CREATE INDEX IF NOT EXISTS idx_snow_inc_group ON snow_closed_incidents (assignment_group);
CREATE INDEX IF NOT EXISTS idx_snow_inc_state ON snow_closed_incidents (state);
CREATE INDEX IF NOT EXISTS idx_snow_inc_resolved ON snow_closed_incidents (resolved_at DESC);

-- Least-privilege reader for the AgentCore Runtime
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ka_reader') THEN
    CREATE ROLE ka_reader WITH LOGIN PASSWORD 'change-me-in-production';
  END IF;
END $$;
GRANT CONNECT ON DATABASE knowledge TO ka_reader;
GRANT USAGE ON SCHEMA public TO ka_reader;
GRANT SELECT ON snow_closed_incidents TO ka_reader;
