from app.db import get_connection


def check_order_facts(company_id: str, shop_id: str, order_id: str) -> dict[str, object]:
    """Read final database facts for development checks; Support Agent never imports this module."""
    with get_connection() as connection:
        row = connection.execute(
            """SELECT p.event_id::text, p.payment_status, r.event_id IS NOT NULL AS receipt_exists,
            t.status AS task_status, t.error_code, m.merchant_order_id IS NOT NULL AS merchant_order_exists
            FROM platform.orders p
            LEFT JOIN merchant.order_event_receipts r ON r.event_id = p.event_id
            LEFT JOIN merchant.order_tasks t ON t.event_id = p.event_id
            LEFT JOIN merchant.orders m ON m.event_id = p.event_id
            WHERE p.company_id = %s AND p.shop_id = %s AND p.external_order_id = %s""",
            (company_id, shop_id, order_id),
        ).fetchone()
    if row is None:
        return {"platform_order_exists": False, "shop_id": shop_id, "order_id": order_id}
    return {"platform_order_exists": True, "shop_id": shop_id, "order_id": order_id, **dict(row)}

