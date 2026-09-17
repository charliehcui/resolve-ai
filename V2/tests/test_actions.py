from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.actions import decide_action, execute_order_recovery, propose_order_recovery, show_action
from app.auth import authenticate
from app.db import create_conversation, get_connection
from app.models import AuthContext
from app.support import handoff_to_support
from app.tools import TOOL_FUNCTIONS, ToolResult
from services import common
from services.common import OrderEvent, OrderRepairRequest
from services.merchant import receive_order_repair, store_order_event
from services.worker import process_next_recovery_task, process_next_task


def create_missing_order_case(auth: AuthContext, order_id: str = "O-RECOVER") -> tuple[str, str, str]:
    event_id = str(uuid4())
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO platform.orders (platform_order_id, event_id, company_id, shop_id, external_order_id, sku, quantity, amount_minor, payment_status, payload_hash) VALUES (%s, %s, %s, 'shop-a', %s, 'SKU-1', 2, 20000, 'paid', %s)",
            (str(uuid4()), event_id, auth.company_id, order_id, f"hash-{event_id}"),
        )
    conversation_id = create_conversation(auth.company_id, auth.user_id)
    _, case_id = handoff_to_support(auth, conversation_id, f"shop-a 的订单 {order_id} 仍未同步", "需要后台调查", [])
    return case_id, event_id, conversation_id


def business_tool(name: str, auth: AuthContext, shop_id: str, order_id: str | None = None) -> ToolResult:
    with get_connection() as connection:
        if name == "GetOrder":
            row = connection.execute("SELECT event_id::text, company_id, shop_id, external_order_id, sku, quantity, amount_minor, payment_status, version FROM platform.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (auth.company_id, shop_id, order_id)).fetchone()
            return ToolResult(tool_name=name, request={"shop_id": shop_id, "order_id": order_id}, response=dict(row) if row else {}, source_service="platform", source_record_id=row["event_id"] if row else None, status="success" if row else "not_found", latency_ms=1)
        if name == "GetShopStatus":
            row = connection.execute("SELECT company_id, shop_id, channel, sync_enabled, version FROM merchant.shops WHERE company_id = %s AND shop_id = %s", (auth.company_id, shop_id)).fetchone()
            return ToolResult(tool_name=name, request={"shop_id": shop_id}, response=dict(row) if row else {}, source_service="merchant", source_record_id=shop_id if row else None, status="success" if row else "not_found", latency_ms=1)
        if name == "CheckConnection":
            row = connection.execute("SELECT company_id, shop_id, channel, connection_status, version FROM merchant.shops WHERE company_id = %s AND shop_id = %s", (auth.company_id, shop_id)).fetchone()
            return ToolResult(tool_name=name, request={"shop_id": shop_id}, response=dict(row) if row else {}, source_service="merchant", source_record_id=shop_id if row else None, status="success" if row else "not_found", latency_ms=1)
        row = connection.execute(
            """SELECT r.event_id::text, r.payload->>'sku' AS platform_sku, (r.payload->>'quantity')::integer AS source_quantity,
            (r.payload->>'amount_minor')::integer AS source_amount_minor, t.status AS task_status, t.error_code,
            m.merchant_order_id::text, m.merchant_sku, m.quantity, m.amount_minor,
            (SELECT COUNT(*) FROM merchant.orders c WHERE c.company_id = r.company_id AND c.shop_id = r.shop_id AND c.external_order_id = r.external_order_id) AS merchant_order_count
            FROM merchant.order_event_receipts r JOIN merchant.order_tasks t ON t.event_id = r.event_id
            LEFT JOIN merchant.orders m ON m.event_id = r.event_id
            WHERE r.company_id = %s AND r.shop_id = %s AND r.external_order_id = %s""",
            (auth.company_id, shop_id, order_id),
        ).fetchone()
    return ToolResult(tool_name=name, request={"shop_id": shop_id, "order_id": order_id}, response=dict(row) if row else {"empty": True}, source_service="merchant", source_record_id=row["event_id"] if row else None, status="success" if row else "empty", latency_ms=1)


@pytest.fixture()
def action_runtime(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(common, "read_service_token", lambda name: "test-service-token")
    monkeypatch.setitem(TOOL_FUNCTIONS, "GetOrder", lambda auth, shop_id, order_id: business_tool("GetOrder", auth, shop_id, order_id))
    monkeypatch.setitem(TOOL_FUNCTIONS, "GetShopStatus", lambda auth, shop_id: business_tool("GetShopStatus", auth, shop_id))
    monkeypatch.setitem(TOOL_FUNCTIONS, "CheckConnection", lambda auth, shop_id: business_tool("CheckConnection", auth, shop_id))
    monkeypatch.setitem(TOOL_FUNCTIONS, "GetProcessRecords", lambda auth, shop_id, order_id: business_tool("GetProcessRecords", auth, shop_id, order_id))
    monkeypatch.setattr("app.actions.get_order", lambda auth, shop_id, order_id: business_tool("GetOrder", auth, shop_id, order_id))
    monkeypatch.setattr("app.actions.get_shop_status", lambda auth, shop_id: business_tool("GetShopStatus", auth, shop_id))

    def fake_mapping(auth: AuthContext, shop_id: str, platform_sku: str) -> ToolResult:
        with get_connection() as connection:
            row = connection.execute("SELECT platform_sku, merchant_sku, active FROM merchant.sku_mappings WHERE company_id = %s AND shop_id = %s AND platform_sku = %s", (auth.company_id, shop_id, platform_sku)).fetchone()
        return ToolResult(tool_name="GetSkuMapping", request={"shop_id": shop_id, "platform_sku": platform_sku}, response=dict(row) if row else {}, source_service="merchant", status="success" if row else "not_found", latency_ms=1)

    monkeypatch.setattr("app.actions.get_sku_mapping", fake_mapping)
    monkeypatch.setattr("app.actions.submit_order_repair", lambda payload: receive_order_repair(OrderRepairRequest(**payload), "test-service-token"))

    def fake_platform_order(company_id: str, shop_id: str, external_order_id: str) -> dict[str, object] | None:
        with get_connection() as connection:
            row = connection.execute("SELECT event_id::text, company_id, shop_id, external_order_id, sku, quantity, amount_minor, payment_status, version FROM platform.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (company_id, shop_id, external_order_id)).fetchone()
        return dict(row) if row else None

    monkeypatch.setattr("services.worker.read_platform_order", fake_platform_order)

    def process_then_verify(auth: AuthContext, action_id: str, timeout_seconds: float = 6) -> dict[str, object]:
        process_next_recovery_task()
        from app.verify import verify_order_recovery

        return verify_order_recovery(auth, action_id, final=True)

    monkeypatch.setattr("app.verify.wait_for_order_verification", process_then_verify)
    return seeded_database


def test_approved_recovery_is_verified_and_duplicate_approval_has_one_effect(action_runtime: dict[str, str]) -> None:
    auth = authenticate(action_runtime["token_a"])
    case_id, event_id, _ = create_missing_order_case(auth)
    proposed = propose_order_recovery(auth, case_id)
    completed = decide_action(auth, proposed["action_id"], "approve")
    duplicate = decide_action(auth, proposed["action_id"], "approve")
    assert completed["status"] == "verified_resolved"
    assert completed["verification"]["status"] == "verified_resolved"
    assert duplicate["status"] == "verified_resolved"
    steps = {step["step_name"]: step for step in completed["steps"]}
    for step_name in ("action_proposal", "policy_check", "scope_version_recheck", "receipt_reconciliation", "verification"):
        assert steps[step_name]["evidence_id"] is not None
    with get_connection() as connection:
        order_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.orders WHERE event_id = %s", (event_id,)).fetchone()["count"]
        repair_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.order_repair_receipts WHERE action_id = %s", (proposed["action_id"],)).fetchone()["count"]
    assert order_count == 1
    assert repair_count == 1


def test_staff_cannot_approve_and_rejection_creates_no_repair(action_runtime: dict[str, str]) -> None:
    admin = authenticate(action_runtime["token_a"])
    staff = authenticate(action_runtime["token_staff_a"])
    other_company = authenticate(action_runtime["token_b"])
    case_id, _, _ = create_missing_order_case(admin, "O-REJECT")
    proposed = propose_order_recovery(admin, case_id)
    with pytest.raises(PermissionError, match="admin"):
        decide_action(staff, proposed["action_id"], "approve")
    with pytest.raises(PermissionError, match="company scope"):
        show_action(other_company, proposed["action_id"])
    rejected = decide_action(admin, proposed["action_id"], "reject")
    assert rejected["status"] == "rejected"
    with get_connection() as connection:
        count = connection.execute("SELECT COUNT(*) AS count FROM merchant.order_repair_receipts WHERE action_id = %s", (proposed["action_id"],)).fetchone()["count"]
    assert count == 0


def test_expired_approval_and_changed_source_do_not_execute(action_runtime: dict[str, str]) -> None:
    auth = authenticate(action_runtime["token_a"])
    expired_case, _, _ = create_missing_order_case(auth, "O-EXPIRED")
    expired = propose_order_recovery(auth, expired_case)
    with get_connection() as connection:
        connection.execute("UPDATE support.action_proposals SET expires_at = %s WHERE action_id = %s", (datetime.now(UTC) - timedelta(seconds=1), expired["action_id"]))
    assert decide_action(auth, expired["action_id"], "approve")["status"] == "expired"
    changed_case, _, _ = create_missing_order_case(auth, "O-CHANGED")
    changed = propose_order_recovery(auth, changed_case)
    with get_connection() as connection:
        connection.execute("UPDATE platform.orders SET payment_status = 'cancelled', version = version + 1 WHERE company_id = %s AND external_order_id = 'O-CHANGED'", (auth.company_id,))
    assert decide_action(auth, changed["action_id"], "approve")["status"] == "blocked"
    with get_connection() as connection:
        count = connection.execute("SELECT COUNT(*) AS count FROM merchant.order_repair_receipts WHERE action_id IN (%s, %s)", (expired["action_id"], changed["action_id"])).fetchone()["count"]
    assert count == 0


def test_persisted_approval_can_execute_after_process_restart(action_runtime: dict[str, str]) -> None:
    auth = authenticate(action_runtime["token_a"])
    case_id, _, _ = create_missing_order_case(auth, "O-RESTART")
    proposed = propose_order_recovery(auth, case_id)
    with get_connection() as connection:
        connection.execute("INSERT INTO support.action_decisions (decision_id, action_id, decision, decided_by) VALUES (%s, %s, 'approved', %s)", (str(uuid4()), proposed["action_id"], auth.user_id))
        connection.execute("UPDATE support.action_proposals SET status = 'approved' WHERE action_id = %s", (proposed["action_id"],))
    result = execute_order_recovery(authenticate(action_runtime["token_a"]), proposed["action_id"])
    assert result["status"] == "verified_resolved", result


def test_repair_contract_deduplicates_action_id(action_runtime: dict[str, str]) -> None:
    approval_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    payload = OrderRepairRequest(action_id=str(uuid4()), request_id=str(uuid4()), company_id="company-a", shop_id="shop-a", external_order_id="O-CONTRACT-REPAIR", source_event_id=str(uuid4()), source_version=1, shop_version=1, source_snapshot={"event_id": "event"}, approval_id=str(uuid4()), approved_by="admin-a", approval_expires_at=approval_expires_at)
    first = receive_order_repair(payload, "test-service-token")
    second = receive_order_repair(payload, "test-service-token")
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["task_id"] == second["task_id"]


def test_enabling_sync_requires_explicit_proposal_scope(action_runtime: dict[str, str]) -> None:
    auth = authenticate(action_runtime["token_a"])
    with get_connection() as connection:
        connection.execute("UPDATE merchant.shops SET sync_enabled = FALSE, version = version + 1 WHERE company_id = %s AND shop_id = 'shop-a'", (auth.company_id,))
    case_id, _, _ = create_missing_order_case(auth, "O-ENABLE-SYNC")
    with pytest.raises(ValueError, match="explicit proposal"):
        propose_order_recovery(auth, case_id)
    proposed = propose_order_recovery(auth, case_id, enable_order_sync=True)
    assert proposed["enable_order_sync"] is True
    result = decide_action(auth, proposed["action_id"], "approve")
    assert result["status"] == "verified_resolved"
    with get_connection() as connection:
        shop = connection.execute("SELECT sync_enabled FROM merchant.shops WHERE company_id = %s AND shop_id = 'shop-a'", (auth.company_id,)).fetchone()
    assert shop["sync_enabled"] is True


def test_existing_correct_order_is_verified_as_no_action_needed(action_runtime: dict[str, str]) -> None:
    auth = authenticate(action_runtime["token_a"])
    case_id, event_id, _ = create_missing_order_case(auth, "O-ALREADY-DONE")
    event = OrderEvent(event_id=event_id, company_id=auth.company_id, shop_id="shop-a", external_order_id="O-ALREADY-DONE", sku="SKU-1", quantity=2, amount_minor=20000, payment_status="paid")
    store_order_event(event)
    assert process_next_task()["status"] == "completed"
    result = propose_order_recovery(auth, case_id)
    assert result["status"] == "no_action_needed"
    with get_connection() as connection:
        proposal_count = connection.execute("SELECT COUNT(*) AS count FROM support.action_proposals WHERE case_id = %s", (case_id,)).fetchone()["count"]
    assert proposal_count == 0
