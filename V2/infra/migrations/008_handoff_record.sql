ALTER TABLE support.handoffs
    DROP COLUMN IF EXISTS customer_answer,
    DROP COLUMN IF EXISTS citations,
    DROP COLUMN IF EXISTS unresolved_reason;
