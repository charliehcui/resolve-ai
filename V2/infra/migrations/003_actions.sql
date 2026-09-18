ALTER TABLE platform.orders ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS support.action_proposals (
    action_id UUID PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES support.cases(case_id),
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    action_type TEXT NOT NULL CHECK (action_type = 'recover_order'),
    status TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected', 'executing', 'awaiting_verification', 'verified_resolved', 'verification_failed', 'blocked', 'expired')),
    source_event_id UUID NOT NULL,
    source_version INTEGER NOT NULL,
    shop_version INTEGER NOT NULL,
    source_snapshot JSONB NOT NULL,
    enable_order_sync BOOLEAN NOT NULL DEFAULT FALSE,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    idempotency_key TEXT NOT NULL UNIQUE,
    proposed_by TEXT NOT NULL REFERENCES support.users(user_id),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support.action_decisions (
    decision_id UUID PRIMARY KEY,
    action_id UUID NOT NULL REFERENCES support.action_proposals(action_id),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    decided_by TEXT NOT NULL REFERENCES support.users(user_id),
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (action_id)
);

CREATE TABLE IF NOT EXISTS support.action_executions (
    execution_id UUID PRIMARY KEY,
    action_id UUID NOT NULL UNIQUE REFERENCES support.action_proposals(action_id),
    request_id UUID NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('claimed', 'submitted', 'failed')),
    claim_until TIMESTAMPTZ NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    receipt JSONB,
    error_type TEXT,
    trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support.action_verifications (
    verification_id UUID PRIMARY KEY,
    action_id UUID NOT NULL UNIQUE REFERENCES support.action_proposals(action_id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'verified_resolved', 'verification_failed')),
    details JSONB NOT NULL,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    trace_id TEXT,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support.action_steps (
    step_id UUID PRIMARY KEY,
    action_id UUID NOT NULL REFERENCES support.action_proposals(action_id),
    step_name TEXT NOT NULL,
    status TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_id UUID REFERENCES support.evidence(evidence_id),
    trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS merchant.order_repair_receipts (
    receipt_id UUID PRIMARY KEY,
    action_id UUID NOT NULL UNIQUE,
    request_id UUID NOT NULL UNIQUE,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    source_event_id UUID NOT NULL,
    source_version INTEGER NOT NULL,
    shop_version INTEGER NOT NULL,
    source_snapshot JSONB NOT NULL,
    enable_order_sync BOOLEAN NOT NULL DEFAULT FALSE,
    approval_id UUID NOT NULL,
    approved_by TEXT NOT NULL,
    approval_expires_at TIMESTAMPTZ NOT NULL,
    request_hash TEXT NOT NULL,
    trace_id TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (company_id, shop_id) REFERENCES merchant.shops(company_id, shop_id)
);

CREATE TABLE IF NOT EXISTS merchant.order_recovery_tasks (
    task_id UUID PRIMARY KEY,
    receipt_id UUID NOT NULL UNIQUE REFERENCES merchant.order_repair_receipts(receipt_id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'blocked', 'failed')),
    error_code TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    result JSONB,
    worker_trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_actions_scope ON support.action_proposals (company_id, action_id);
CREATE INDEX IF NOT EXISTS idx_action_steps_action ON support.action_steps (action_id, created_at);
CREATE INDEX IF NOT EXISTS idx_recovery_tasks_status ON merchant.order_recovery_tasks (status, created_at);
