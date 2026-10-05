CREATE SCHEMA IF NOT EXISTS platform;
CREATE SCHEMA IF NOT EXISTS merchant;

CREATE TABLE IF NOT EXISTS platform.orders (
    platform_order_id UUID PRIMARY KEY,
    event_id UUID NOT NULL UNIQUE,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
    payment_status TEXT NOT NULL CHECK (payment_status IN ('paid', 'unpaid', 'cancelled')),
    payload_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, shop_id, external_order_id)
);

CREATE TABLE IF NOT EXISTS platform.event_deliveries (
    delivery_id UUID PRIMARY KEY,
    event_id UUID NOT NULL REFERENCES platform.orders(event_id),
    status TEXT NOT NULL CHECK (status IN ('delivered', 'failed')),
    http_status INTEGER,
    error_type TEXT,
    trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS merchant.shops (
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    shop_id TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('A', 'B')),
    sync_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    connection_status TEXT NOT NULL DEFAULT 'authorized',
    version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (company_id, shop_id)
);

CREATE TABLE IF NOT EXISTS merchant.sku_mappings (
    company_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    platform_sku TEXT NOT NULL,
    merchant_sku TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (company_id, shop_id, platform_sku),
    FOREIGN KEY (company_id, shop_id) REFERENCES merchant.shops(company_id, shop_id)
);

CREATE TABLE IF NOT EXISTS merchant.order_event_receipts (
    event_id UUID PRIMARY KEY,
    company_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    trace_id TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (company_id, shop_id) REFERENCES merchant.shops(company_id, shop_id)
);

CREATE TABLE IF NOT EXISTS merchant.order_tasks (
    task_id UUID PRIMARY KEY,
    event_id UUID NOT NULL UNIQUE REFERENCES merchant.order_event_receipts(event_id),
    company_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'blocked', 'failed')),
    error_code TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    worker_trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS merchant.orders (
    merchant_order_id UUID PRIMARY KEY,
    event_id UUID NOT NULL UNIQUE REFERENCES merchant.order_event_receipts(event_id),
    company_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    external_order_id TEXT NOT NULL,
    merchant_sku TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    amount_minor INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, shop_id, external_order_id)
);

CREATE INDEX IF NOT EXISTS idx_platform_orders_scope ON platform.orders (company_id, shop_id, external_order_id);
CREATE INDEX IF NOT EXISTS idx_merchant_tasks_status ON merchant.order_tasks (status, created_at);
CREATE INDEX IF NOT EXISTS idx_merchant_orders_scope ON merchant.orders (company_id, shop_id, external_order_id);

ALTER TABLE platform.event_deliveries ADD COLUMN IF NOT EXISTS trace_id TEXT;
ALTER TABLE merchant.order_event_receipts ADD COLUMN IF NOT EXISTS trace_id TEXT;
ALTER TABLE merchant.order_tasks ADD COLUMN IF NOT EXISTS worker_trace_id TEXT;
