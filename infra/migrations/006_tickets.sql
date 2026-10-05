ALTER TABLE support.users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE support.users ADD CONSTRAINT users_role_check CHECK (role IN ('admin', 'staff', 'engineer'));

CREATE TABLE IF NOT EXISTS support.ticket_assignment_rules (
    company_id TEXT PRIMARY KEY REFERENCES support.companies(company_id),
    engineer_user_id TEXT NOT NULL REFERENCES support.users(user_id),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support.tickets (
    ticket_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES support.conversations(conversation_id),
    case_id UUID REFERENCES support.cases(case_id),
    handoff_id UUID REFERENCES support.handoffs(handoff_id),
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    category TEXT NOT NULL CHECK (category IN ('order', 'shipment', 'stock', 'general')),
    trigger TEXT NOT NULL CHECK (trigger IN ('support_unresolved', 'budget_reached', 'evidence_insufficient', 'unknown_error', 'user_requested')),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'closed')),
    product_version TEXT NOT NULL,
    customer_problem TEXT NOT NULL,
    business_target JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_successful_step JSONB,
    failed_evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    attempted_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    confirmed_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
    possible_causes JSONB NOT NULL DEFAULT '[]'::jsonb,
    excluded_causes JSONB NOT NULL DEFAULT '[]'::jsonb,
    unknowns JSONB NOT NULL DEFAULT '[]'::jsonb,
    next_steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    current_result TEXT NOT NULL,
    created_by TEXT NOT NULL REFERENCES support.users(user_id),
    assigned_to TEXT REFERENCES support.users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_open_ticket_per_conversation ON support.tickets (conversation_id) WHERE status IN ('open', 'in_progress');
CREATE INDEX IF NOT EXISTS idx_tickets_company_status ON support.tickets (company_id, status, created_at);

CREATE TABLE IF NOT EXISTS support.ticket_read_grants (
    ticket_id UUID NOT NULL REFERENCES support.tickets(ticket_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES support.users(user_id),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticket_id, user_id)
);
