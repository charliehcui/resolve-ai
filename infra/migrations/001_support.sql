CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS support;

CREATE TABLE IF NOT EXISTS support.companies (
    company_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS support.users (
    user_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'staff'))
);

CREATE TABLE IF NOT EXISTS support.access_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES support.users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS support.conversations (
    conversation_id UUID PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    user_id TEXT NOT NULL REFERENCES support.users(user_id),
    active_role TEXT NOT NULL DEFAULT 'CUSTOMER' CHECK (active_role IN ('CUSTOMER', 'SUPPORT')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support.messages (
    message_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES support.conversations(conversation_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE support.messages ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS support.product_documents (
    document_id UUID PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    title TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    product TEXT NOT NULL,
    version TEXT NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    UNIQUE (company_id, source_uri, version)
);

CREATE TABLE IF NOT EXISTS support.document_chunks (
    chunk_id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES support.product_documents(document_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding vector(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, content_hash)
);

CREATE TABLE IF NOT EXISTS support.retrieval_runs (
    retrieval_id UUID PRIMARY KEY,
    conversation_id UUID REFERENCES support.conversations(conversation_id) ON DELETE SET NULL,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    query TEXT NOT NULL,
    chunk_ids JSONB NOT NULL,
    latency_ms INTEGER NOT NULL,
    trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE support.retrieval_runs ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'vector_only';
ALTER TABLE support.retrieval_runs ADD COLUMN IF NOT EXISTS search_query TEXT;
ALTER TABLE support.retrieval_runs ADD COLUMN IF NOT EXISTS filters JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE support.retrieval_runs ADD COLUMN IF NOT EXISTS vector_ranks JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE support.retrieval_runs ADD COLUMN IF NOT EXISTS keyword_ranks JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE support.retrieval_runs ADD COLUMN IF NOT EXISTS fused_ranks JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE support.retrieval_runs ADD COLUMN IF NOT EXISTS rerank_ranks JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE support.retrieval_runs ADD COLUMN IF NOT EXISTS rerank_model TEXT;
ALTER TABLE support.retrieval_runs ADD COLUMN IF NOT EXISTS rerank_latency_ms INTEGER;
ALTER TABLE support.retrieval_runs ADD COLUMN IF NOT EXISTS rerank_error TEXT;

CREATE TABLE IF NOT EXISTS support.agent_runs (
    run_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES support.conversations(conversation_id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    purpose TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    cost NUMERIC,
    trace_id TEXT,
    error_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON support.messages (conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chunks_company ON support.document_chunks (company_id);
CREATE INDEX IF NOT EXISTS idx_runs_conversation ON support.agent_runs (conversation_id, created_at);
