-- Phase 7 channel resilience and stock data flow.
ALTER TABLE support.handoffs ADD COLUMN IF NOT EXISTS known_sku TEXT;

ALTER TABLE merchant.order_tasks ADD COLUMN IF NOT EXISTS http_status INTEGER;
ALTER TABLE merchant.order_tasks ADD COLUMN IF NOT EXISTS failure_request_id UUID;
ALTER TABLE merchant.shipment_tasks ADD COLUMN IF NOT EXISTS http_status INTEGER;
ALTER TABLE merchant.shipment_tasks ADD COLUMN IF NOT EXISTS failure_request_id UUID;

CREATE TABLE IF NOT EXISTS merchant.channel_failures (
    failure_id UUID PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    shop_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    operation TEXT NOT NULL,
    http_status INTEGER,
    error_code TEXT NOT NULL,
    request_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (company_id, shop_id) REFERENCES merchant.shops(company_id, shop_id)
);

CREATE TABLE IF NOT EXISTS warehouse.stock_items (
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    warehouse_sku TEXT NOT NULL,
    physical_quantity INTEGER NOT NULL CHECK (physical_quantity >= 0),
    reserved_quantity INTEGER NOT NULL CHECK (reserved_quantity >= 0),
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (company_id, warehouse_sku)
);

CREATE TABLE IF NOT EXISTS merchant.stock_rules (
    company_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    platform_sku TEXT NOT NULL,
    warehouse_sku TEXT NOT NULL,
    safety_stock INTEGER NOT NULL DEFAULT 0 CHECK (safety_stock >= 0),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (company_id, shop_id, platform_sku),
    FOREIGN KEY (company_id, shop_id) REFERENCES merchant.shops(company_id, shop_id)
);

CREATE TABLE IF NOT EXISTS merchant.stock_publish_tasks (
    task_id UUID PRIMARY KEY,
    request_id UUID NOT NULL UNIQUE,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    shop_id TEXT NOT NULL,
    platform_sku TEXT NOT NULL,
    warehouse_sku TEXT NOT NULL,
    warehouse_version INTEGER NOT NULL,
    physical_quantity INTEGER NOT NULL,
    reserved_quantity INTEGER NOT NULL,
    safety_stock INTEGER NOT NULL,
    expected_quantity INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'blocked')),
    attempts INTEGER NOT NULL DEFAULT 0,
    http_status INTEGER,
    error_code TEXT,
    failure_request_id UUID,
    platform_receipt JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, shop_id, platform_sku, warehouse_version),
    FOREIGN KEY (company_id, shop_id, platform_sku) REFERENCES merchant.stock_rules(company_id, shop_id, platform_sku)
);

CREATE TABLE IF NOT EXISTS platform.stock_levels (
    platform_stock_id UUID PRIMARY KEY,
    request_id UUID NOT NULL UNIQUE,
    company_id TEXT NOT NULL REFERENCES support.companies(company_id),
    shop_id TEXT NOT NULL,
    platform_sku TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    source_version INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, shop_id, platform_sku)
);

CREATE INDEX IF NOT EXISTS idx_channel_failures_scope ON merchant.channel_failures (company_id, shop_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stock_tasks_status ON merchant.stock_publish_tasks (status, created_at);
CREATE INDEX IF NOT EXISTS idx_platform_stock_scope ON platform.stock_levels (company_id, shop_id, platform_sku);
