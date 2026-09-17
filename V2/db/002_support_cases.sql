CREATE TABLE IF NOT EXISTS support.handoffs (
    handoff_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL UNIQUE REFERENCES support.conversations(conversation_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    customer_problem TEXT NOT NULL,
    customer_answer TEXT NOT NULL,
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    attempted_steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    known_shop_id TEXT,
    known_order_id TEXT,
    unresolved_reason TEXT NOT NULL,
    missing_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support.cases (
    case_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL UNIQUE REFERENCES support.conversations(conversation_id) ON DELETE CASCADE,
    handoff_id UUID NOT NULL UNIQUE REFERENCES support.handoffs(handoff_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    status TEXT NOT NULL DEFAULT 'investigating' CHECK (status IN ('investigating', 'needs_info', 'diagnosed', 'pending_human')),
    outcome TEXT,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    total_latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support.evidence (
    evidence_id UUID PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES support.cases(case_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    sequence INTEGER NOT NULL,
    batch_id UUID NOT NULL,
    parallel BOOLEAN NOT NULL DEFAULT FALSE,
    model_tool_call_id TEXT,
    tool_name TEXT NOT NULL,
    request JSONB NOT NULL,
    response JSONB NOT NULL,
    source_service TEXT NOT NULL,
    source_record_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('success', 'empty', 'not_found', 'forbidden', 'unavailable', 'error')),
    latency_ms INTEGER NOT NULL,
    trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (case_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_evidence_case ON support.evidence (case_id, sequence);

