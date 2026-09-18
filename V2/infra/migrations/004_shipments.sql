CREATE SCHEMA IF NOT EXISTS warehouse;

ALTER TABLE support.evidence ADD COLUMN IF NOT EXISTS object_type TEXT;
ALTER TABLE support.evidence ADD COLUMN IF NOT EXISTS object_id TEXT;
ALTER TABLE support.evidence ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE support.evidence ADD COLUMN IF NOT EXISTS source_version INTEGER;

ALTER TABLE merchant.shops ADD COLUMN IF NOT EXISTS shipment_sync_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE support.action_proposals ADD COLUMN IF NOT EXISTS enable_shipment_sync BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE support.action_proposals DROP CONSTRAINT IF EXISTS action_proposals_action_type_check;
ALTER TABLE support.action_proposals ADD CONSTRAINT action_proposals_action_type_check CHECK (action_type IN ('recover_order', 'recover_shipment'));
ALTER TABLE support.action_executions DROP CONSTRAINT IF EXISTS action_executions_status_check;
ALTER TABLE support.action_executions ADD CONSTRAINT action_executions_status_check CHECK (status IN ('claimed', 'submitted', 'unknown', 'failed'));

CREATE TABLE IF NOT EXISTS merchant.warehouse_dispatch_tasks (
    task_id UUID PRIMARY KEY,
    merchant_order_id UUID NOT NULL UNIQUE REFERENCES merchant.orders(merchant_order_id),
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    receipt JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS warehouse.orders (
    warehouse_order_id UUID PRIMARY KEY,
    merchant_order_id UUID NOT NULL UNIQUE,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    merchant_sku TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    status TEXT NOT NULL DEFAULT 'awaiting_shipment' CHECK (status IN ('awaiting_shipment', 'shipped')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, shop_id, external_order_id)
);

CREATE TABLE IF NOT EXISTS warehouse.shipments (
    shipment_id UUID PRIMARY KEY,
    warehouse_order_id UUID NOT NULL UNIQUE REFERENCES warehouse.orders(warehouse_order_id),
    company_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    carrier TEXT NOT NULL,
    tracking_number TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    shipped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, carrier, tracking_number)
);

CREATE TABLE IF NOT EXISTS warehouse.shipment_deliveries (
    delivery_id UUID PRIMARY KEY,
    shipment_id UUID NOT NULL REFERENCES warehouse.shipments(shipment_id),
    status TEXT NOT NULL CHECK (status IN ('delivered', 'failed')),
    http_status INTEGER,
    response JSONB,
    error_type TEXT,
    trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS merchant.shipment_event_receipts (
    shipment_id UUID PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    trace_id TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS merchant.shipment_tasks (
    task_id UUID PRIMARY KEY,
    shipment_id UUID NOT NULL UNIQUE REFERENCES merchant.shipment_event_receipts(shipment_id),
    company_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'blocked', 'failed', 'unknown')),
    error_code TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    request_id UUID NOT NULL UNIQUE,
    platform_receipt JSONB,
    worker_trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS merchant.shipments (
    merchant_shipment_id UUID PRIMARY KEY,
    shipment_id UUID NOT NULL UNIQUE REFERENCES merchant.shipment_event_receipts(shipment_id),
    company_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    carrier TEXT NOT NULL,
    tracking_number TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, shop_id, external_order_id)
);

CREATE TABLE IF NOT EXISTS platform.shipments (
    platform_shipment_id UUID PRIMARY KEY,
    request_id UUID NOT NULL UNIQUE,
    shipment_id UUID NOT NULL UNIQUE,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    carrier TEXT NOT NULL,
    tracking_number TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'shipped' CHECK (status = 'shipped'),
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, shop_id, external_order_id)
);

CREATE TABLE IF NOT EXISTS platform.response_delay_controls (
    company_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('shipment')),
    remaining INTEGER NOT NULL DEFAULT 1 CHECK (remaining >= 0),
    delay_seconds NUMERIC NOT NULL DEFAULT 6 CHECK (delay_seconds > 0),
    PRIMARY KEY (company_id, shop_id, operation)
);

CREATE TABLE IF NOT EXISTS merchant.shipment_repair_receipts (
    receipt_id UUID PRIMARY KEY,
    action_id UUID NOT NULL UNIQUE,
    request_id UUID NOT NULL UNIQUE,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    shipment_id UUID NOT NULL,
    shipment_version INTEGER NOT NULL,
    source_snapshot JSONB NOT NULL,
    enable_shipment_sync BOOLEAN NOT NULL DEFAULT FALSE,
    approval_id UUID NOT NULL,
    approved_by TEXT NOT NULL,
    approval_expires_at TIMESTAMPTZ NOT NULL,
    request_hash TEXT NOT NULL,
    trace_id TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (company_id, shop_id) REFERENCES merchant.shops(company_id, shop_id)
);

CREATE TABLE IF NOT EXISTS merchant.shipment_recovery_tasks (
    task_id UUID PRIMARY KEY,
    receipt_id UUID NOT NULL UNIQUE REFERENCES merchant.shipment_repair_receipts(receipt_id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'blocked', 'failed', 'unknown')),
    error_code TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    result JSONB,
    worker_trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dispatch_tasks_status ON merchant.warehouse_dispatch_tasks (status, created_at);
CREATE INDEX IF NOT EXISTS idx_shipment_tasks_status ON merchant.shipment_tasks (status, created_at);
CREATE INDEX IF NOT EXISTS idx_warehouse_orders_scope ON warehouse.orders (company_id, shop_id, external_order_id);
CREATE INDEX IF NOT EXISTS idx_platform_shipments_scope ON platform.shipments (company_id, shop_id, external_order_id);
CREATE INDEX IF NOT EXISTS idx_shipment_recovery_tasks_status ON merchant.shipment_recovery_tasks (status, created_at);
