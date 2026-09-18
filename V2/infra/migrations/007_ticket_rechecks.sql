CREATE TABLE IF NOT EXISTS support.ticket_rechecks (
    recheck_id UUID PRIMARY KEY,
    ticket_id UUID NOT NULL REFERENCES support.tickets(ticket_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('RESOLVED', 'UNRESOLVED', 'NEEDS_INFO')),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    checked_by TEXT NOT NULL REFERENCES support.users(user_id),
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ticket_rechecks_ticket_time ON support.ticket_rechecks (ticket_id, checked_at);
