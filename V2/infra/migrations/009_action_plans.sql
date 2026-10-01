-- Keep existing table names while extending the domain to Action Plans.
ALTER TABLE support.action_proposals DROP CONSTRAINT IF EXISTS action_proposals_action_type_check;
ALTER TABLE support.action_proposals ADD CONSTRAINT action_proposals_action_type_check CHECK (action_type IN ('recover_order', 'recover_shipment', 'retry_order_sync', 'resend_shipment', 'refresh_inventory', 'retry_failed_task'));
ALTER TABLE support.action_proposals ALTER COLUMN source_event_id DROP NOT NULL;
ALTER TABLE support.cases DROP CONSTRAINT IF EXISTS cases_status_check;
ALTER TABLE support.cases ADD CONSTRAINT cases_status_check CHECK (status IN ('investigating', 'needs_info', 'diagnosed', 'pending_human', 'awaiting_confirmation', 'user_action_required', 'retry_later', 'verified_resolved'));
ALTER TABLE merchant.order_tasks ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE merchant.shops ADD COLUMN IF NOT EXISTS order_fault TEXT;
ALTER TABLE merchant.shops ADD COLUMN IF NOT EXISTS shipment_fault TEXT;

CREATE TABLE IF NOT EXISTS merchant.action_repair_receipts (
    action_id UUID PRIMARY KEY,
    request_id UUID NOT NULL UNIQUE,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    action_type TEXT NOT NULL CHECK (action_type IN ('refresh_inventory', 'retry_failed_task')),
    request_hash TEXT NOT NULL,
    receipt JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
