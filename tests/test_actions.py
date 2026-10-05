from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from backend.app.auth import authenticate
from backend.app.database import create_conversation, get_connection
from backend.app.handoff import create_support_handoff
from backend.app.models import UserContext
from backend.app.support_action_approvals import decide_action_plan, get_action_details
from backend.app.support_action_execution import execute_order_recovery
from backend.app.support_action_plans import build_order_action_plan
from backend.app.support_action_verification import wait_for_order_verification as real_wait_for_order_verification
from backend.app.support_tools import READ_TOOL_FUNCTIONS, ReadToolResult
from simulator.services import common
from simulator.services.common import OrderEvent, OrderRepairRequest
from simulator.services.merchant import receive_order_repair, store_order_event
from simulator.services.worker import process_next_recovery_task, process_next_task


def test_unknown_execution_keeps_live_lease_and_reuses_execution_after_expiry(action_runtime: dict[str, str]) -> None:
    from backend.app.support_action_execution import acquire_action_execution

    user = authenticate(action_runtime['token_a'])
    case_id, _, _ = create_missing_order_case(user, 'O-UNKNOWN-LEASE')
    action = build_order_action_plan(user, case_id)
    action_id = action['action_id']
    request_id = str(uuid4())
    with get_connection() as connection:
        connection.execute("INSERT INTO support.action_decisions (decision_id, action_id, decision, decided_by) VALUES (%s, %s, 'approved', %s)", (str(uuid4()), action_id, user.user_id))
        connection.execute("UPDATE support.action_proposals SET status = 'approved' WHERE action_id = %s", (action_id,))
    assert acquire_action_execution(action_id, request_id) == 'acquired'
    with get_connection() as connection:
        connection.execute("UPDATE support.action_executions SET status = 'unknown' WHERE action_id = %s", (action_id,))
        original = connection.execute('SELECT execution_id, request_id, attempts FROM support.action_executions WHERE action_id = %s', (action_id,)).fetchone()
    assert acquire_action_execution(action_id, request_id) == 'busy'
    with get_connection() as connection:
        unchanged = connection.execute('SELECT execution_id, request_id, attempts FROM support.action_executions WHERE action_id = %s', (action_id,)).fetchone()
        assert unchanged == original
        connection.execute("UPDATE support.action_executions SET claim_until = NOW() - INTERVAL '1 second' WHERE action_id = %s", (action_id,))
    assert acquire_action_execution(action_id, request_id) == 'acquired'
    with get_connection() as connection:
        retried = connection.execute('SELECT execution_id, request_id, attempts FROM support.action_executions WHERE action_id = %s', (action_id,)).fetchone()
    assert retried['execution_id'] == original['execution_id'] and retried['request_id'] == original['request_id']
    assert retried['attempts'] == original['attempts'] + 1


@pytest.mark.parametrize('changed_source', [False, True])
def test_verification_deadline_preserves_pending_until_actual_worker_outcome(action_runtime: dict[str, str], monkeypatch: pytest.MonkeyPatch, changed_source: bool) -> None:
    user = authenticate(action_runtime['token_a'])
    case_id, event_id, _ = create_missing_order_case(user, 'O-PENDING-VERIFY')
    action = build_order_action_plan(user, case_id)
    action_id = action['action_id']
    monkeypatch.setattr('backend.app.support_action_verification.wait_for_order_verification', lambda user, action_id: get_action_details(user, action_id))
    assert decide_action_plan(user, action_id, 'approve')['status'] == 'awaiting_verification'

    def actual_receipt(action: dict) -> dict:
        with get_connection() as connection:
            return dict(connection.execute('SELECT t.status AS task_status FROM merchant.order_repair_receipts r JOIN merchant.order_recovery_tasks t ON t.receipt_id = r.receipt_id WHERE r.action_id = %s', (action['action_id'],)).fetchone())

    monkeypatch.setattr('backend.app.support_action_execution.get_existing_recovery_receipt', actual_receipt)
    waiting = real_wait_for_order_verification(user, action_id, timeout_seconds=0)
    assert waiting['status'] == 'awaiting_verification' and waiting['verification']['status'] == 'pending'
    if changed_source:
        with get_connection() as connection:
            connection.execute("UPDATE platform.orders SET payment_status = 'cancelled', version = version + 1 WHERE event_id = %s", (event_id,))
    process_next_recovery_task()
    finished = real_wait_for_order_verification(user, action_id, timeout_seconds=0)
    assert finished['status'] == ('verification_failed' if changed_source else 'verified_resolved')
    with get_connection() as connection:
        assert connection.execute('SELECT COUNT(*) AS count FROM merchant.orders WHERE event_id = %s', (event_id,)).fetchone()['count'] == (0 if changed_source else 1)
        assert connection.execute('SELECT COUNT(*) AS count FROM support.action_executions WHERE action_id = %s', (action_id,)).fetchone()['count'] == 1


def create_missing_order_case(user: UserContext, order_id: str = "O-RECOVER") -> tuple[str, str, str]:
    event_id = str(uuid4())
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO platform.orders (platform_order_id, event_id, company_id, shop_id, external_order_id, sku, quantity, amount_minor, payment_status, payload_hash) VALUES (%s, %s, %s, 'shop-a', %s, 'SKU-1', 2, 20000, 'paid', %s)",
            (str(uuid4()), event_id, user.company_id, order_id, f"hash-{event_id}"),
        )
    conversation_id = create_conversation(user.company_id, user.user_id)
    _, case_id = create_support_handoff(user, conversation_id, f"shop-a 的订单 {order_id} 仍未同步", [])
    return case_id, event_id, conversation_id


def business_tool(name: str, user: UserContext, shop_id: str, order_id: str | None = None) -> ReadToolResult:
    with get_connection() as connection:
        if name == "GetOrder":
            row = connection.execute("SELECT event_id::text, company_id, shop_id, external_order_id, sku, quantity, amount_minor, payment_status, version FROM platform.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (user.company_id, shop_id, order_id)).fetchone()
            return ReadToolResult(tool_name=name, request={"shop_id": shop_id, "order_id": order_id}, response=dict(row) if row else {}, source_service="platform", source_record_id=row["event_id"] if row else None, status="success" if row else "not_found", latency_ms=1)
        if name == "GetShopSyncStatus":
            row = connection.execute("SELECT company_id, shop_id, channel, sync_enabled, version FROM merchant.shops WHERE company_id = %s AND shop_id = %s", (user.company_id, shop_id)).fetchone()
            return ReadToolResult(tool_name=name, request={"shop_id": shop_id}, response=dict(row) if row else {}, source_service="merchant", source_record_id=shop_id if row else None, status="success" if row else "not_found", latency_ms=1)
        if name == "GetShopConnectionStatus":
            row = connection.execute("SELECT company_id, shop_id, channel, connection_status, version FROM merchant.shops WHERE company_id = %s AND shop_id = %s", (user.company_id, shop_id)).fetchone()
            return ReadToolResult(tool_name=name, request={"shop_id": shop_id}, response=dict(row) if row else {}, source_service="merchant", source_record_id=shop_id if row else None, status="success" if row else "not_found", latency_ms=1)
        row = connection.execute(
            """SELECT r.event_id::text, r.payload->>'sku' AS platform_sku, (r.payload->>'quantity')::integer AS source_quantity,
            (r.payload->>'amount_minor')::integer AS source_amount_minor, t.status AS task_status, t.error_code,
            m.merchant_order_id::text, m.merchant_sku, m.quantity, m.amount_minor,
            (SELECT COUNT(*) FROM merchant.orders c WHERE c.company_id = r.company_id AND c.shop_id = r.shop_id AND c.external_order_id = r.external_order_id) AS merchant_order_count
            FROM merchant.order_event_receipts r JOIN merchant.order_tasks t ON t.event_id = r.event_id
            LEFT JOIN merchant.orders m ON m.event_id = r.event_id
            WHERE r.company_id = %s AND r.shop_id = %s AND r.external_order_id = %s""",
            (user.company_id, shop_id, order_id),
        ).fetchone()
    return ReadToolResult(tool_name=name, request={"shop_id": shop_id, "order_id": order_id}, response=dict(row) if row else {"empty": True}, source_service="merchant", source_record_id=row["event_id"] if row else None, status="success" if row else "empty", latency_ms=1)


@pytest.fixture()
def action_runtime(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(common, "read_service_token", lambda name: "test-service-token")
    monkeypatch.setitem(READ_TOOL_FUNCTIONS, "GetOrder", lambda user, shop_id, order_id: business_tool("GetOrder", user, shop_id, order_id))
    monkeypatch.setitem(READ_TOOL_FUNCTIONS, "GetShopSyncStatus", lambda user, shop_id: business_tool("GetShopSyncStatus", user, shop_id))
    monkeypatch.setitem(READ_TOOL_FUNCTIONS, "GetShopConnectionStatus", lambda user, shop_id: business_tool("GetShopConnectionStatus", user, shop_id))
    monkeypatch.setitem(READ_TOOL_FUNCTIONS, "GetOrderProcessRecords", lambda user, shop_id, order_id: business_tool("GetOrderProcessRecords", user, shop_id, order_id))
    monkeypatch.setattr("backend.app.support_action_execution.get_order", lambda user, shop_id, order_id: business_tool("GetOrder", user, shop_id, order_id))
    monkeypatch.setattr("backend.app.support_action_execution.get_shop_sync_status", lambda user, shop_id: business_tool("GetShopSyncStatus", user, shop_id))
    monkeypatch.setattr('backend.app.support_action_execution.get_shop_connection_status', lambda user, shop_id: business_tool('GetShopConnectionStatus', user, shop_id))

    def fake_mapping(user: UserContext, shop_id: str, platform_sku: str) -> ReadToolResult:
        with get_connection() as connection:
            row = connection.execute("SELECT platform_sku, merchant_sku, active FROM merchant.sku_mappings WHERE company_id = %s AND shop_id = %s AND platform_sku = %s", (user.company_id, shop_id, platform_sku)).fetchone()
        return ReadToolResult(tool_name="GetSkuMapping", request={"shop_id": shop_id, "platform_sku": platform_sku}, response=dict(row) if row else {}, source_service="merchant", status="success" if row else "not_found", latency_ms=1)

    monkeypatch.setattr("backend.app.support_action_plans.get_sku_mapping", fake_mapping)
    monkeypatch.setattr("backend.app.support_action_execution.submit_order_repair", lambda payload: receive_order_repair(OrderRepairRequest(**payload), "test-service-token"))

    def fake_platform_order(company_id: str, shop_id: str, external_order_id: str) -> dict[str, object] | None:
        with get_connection() as connection:
            row = connection.execute("SELECT event_id::text, company_id, shop_id, external_order_id, sku, quantity, amount_minor, payment_status, version FROM platform.orders WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (company_id, shop_id, external_order_id)).fetchone()
        return dict(row) if row else None

    monkeypatch.setattr("simulator.services.worker.read_platform_order", fake_platform_order)

    def process_then_verify(user: UserContext, action_id: str, timeout_seconds: float = 6) -> dict[str, object]:
        process_next_recovery_task()
        from backend.app.support_action_verification import verify_order_recovery

        return verify_order_recovery(user, action_id, final_check=True)

    monkeypatch.setattr("backend.app.support_action_verification.wait_for_order_verification", process_then_verify)
    return seeded_database


def test_approved_recovery_is_verified_and_duplicate_approval_has_one_effect(action_runtime: dict[str, str]) -> None:
    user = authenticate(action_runtime["token_a"])
    case_id, event_id, _ = create_missing_order_case(user)
    proposed = build_order_action_plan(user, case_id)
    completed = decide_action_plan(user, proposed["action_id"], "approve")
    duplicate = decide_action_plan(user, proposed["action_id"], "approve")
    assert completed["status"] == "verified_resolved"
    assert completed["verification"]["status"] == "verified_resolved"
    assert duplicate["status"] == "verified_resolved"
    steps = {step["step_name"]: step for step in completed["steps"]}
    for step_name in ("action_plan", "policy_check", "scope_version_recheck", "receipt_reconciliation", "verification"):
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
    proposed = build_order_action_plan(admin, case_id)
    with pytest.raises(PermissionError, match="scope"):
        decide_action_plan(staff, proposed["action_id"], "approve")
    with pytest.raises(PermissionError, match="company scope"):
        get_action_details(other_company, proposed["action_id"])
    rejected = decide_action_plan(admin, proposed["action_id"], "reject")
    assert rejected["status"] == "rejected"
    with get_connection() as connection:
        count = connection.execute("SELECT COUNT(*) AS count FROM merchant.order_repair_receipts WHERE action_id = %s", (proposed["action_id"],)).fetchone()["count"]
    assert count == 0


def test_expired_approval_and_changed_source_do_not_execute(action_runtime: dict[str, str]) -> None:
    user = authenticate(action_runtime["token_a"])
    expired_case, _, _ = create_missing_order_case(user, "O-EXPIRED")
    expired = build_order_action_plan(user, expired_case)
    with get_connection() as connection:
        connection.execute("UPDATE support.action_proposals SET expires_at = %s WHERE action_id = %s", (datetime.now(UTC) - timedelta(seconds=1), expired["action_id"]))
    assert decide_action_plan(user, expired["action_id"], "approve")["status"] == "expired"
    changed_case, _, _ = create_missing_order_case(user, "O-CHANGED")
    changed = build_order_action_plan(user, changed_case)
    with get_connection() as connection:
        connection.execute("UPDATE platform.orders SET payment_status = 'cancelled', version = version + 1 WHERE company_id = %s AND external_order_id = 'O-CHANGED'", (user.company_id,))
    assert decide_action_plan(user, changed["action_id"], "approve")["status"] == "blocked"
    with get_connection() as connection:
        count = connection.execute("SELECT COUNT(*) AS count FROM merchant.order_repair_receipts WHERE action_id IN (%s, %s)", (expired["action_id"], changed["action_id"])).fetchone()["count"]
    assert count == 0


def test_changed_authorization_without_version_change_blocks_before_submission(action_runtime: dict[str, str]) -> None:
    user = authenticate(action_runtime['token_a'])
    case_id, _, _ = create_missing_order_case(user, 'O-AUTH-CHANGED')
    proposed = build_order_action_plan(user, case_id)
    with get_connection() as connection:
        connection.execute("INSERT INTO support.action_decisions (decision_id, action_id, decision, decided_by) VALUES (%s, %s, 'approved', %s)", (str(uuid4()), proposed['action_id'], user.user_id))
        connection.execute("UPDATE support.action_proposals SET status = 'approved' WHERE action_id = %s", (proposed['action_id'],))
        connection.execute("UPDATE merchant.shops SET connection_status = 'auth_expired' WHERE company_id = %s AND shop_id = 'shop-a'", (user.company_id,))
    result = execute_order_recovery(user, proposed['action_id'])
    assert result['status'] == 'blocked'
    with get_connection() as connection:
        assert connection.execute('SELECT COUNT(*) AS count FROM merchant.order_repair_receipts WHERE action_id = %s', (proposed['action_id'],)).fetchone()['count'] == 0
        assert connection.execute('SELECT COUNT(*) AS count FROM support.action_executions WHERE action_id = %s', (proposed['action_id'],)).fetchone()['count'] == 0


def test_decision_snapshot_is_reused_but_authorization_is_fresh_before_write(action_runtime, monkeypatch):
    from backend.app.support_action_plans import create_action_plan
    from backend.app.support_diagnosis import CandidateAction
    from backend.app.support_tools import execute_read_tool_batch
    user = authenticate(action_runtime['token_a'])
    case_id, _, _ = create_missing_order_case(user, 'O-SNAPSHOT')
    calls = [{"name": name, "args": {"shop_id": "shop-a", "order_id": "O-SNAPSHOT"} if name in {"GetOrder", "GetOrderProcessRecords"} else {"shop_id": "shop-a"}} for name in ["GetOrder", "GetOrderProcessRecords", "GetShopSyncStatus", "GetShopConnectionStatus"]]
    snapshot = execute_read_tool_batch(case_id, user, calls, "shop-a", "O-SNAPSHOT")
    monkeypatch.setattr('backend.app.support_action_plans.execute_read_tool_batch', lambda *args: pytest.fail('Decision snapshot must not be re-read by the plan builder'))
    action = create_action_plan(user, case_id, CandidateAction(action_type='retry_order_sync', reason='Current missing import', evidence_ids=[record.evidence_id for record in snapshot]), decision_evidence=snapshot)
    assert {record.evidence_id for record in snapshot}.issubset(set(action['evidence_ids']))
    with get_connection() as connection:
        connection.execute("INSERT INTO support.action_decisions (decision_id, action_id, decision, decided_by) VALUES (%s, %s, 'approved', %s)", (str(uuid4()), action['action_id'], user.user_id))
        connection.execute("UPDATE support.action_proposals SET status = 'approved' WHERE action_id = %s", (action['action_id'],))
        connection.execute("UPDATE merchant.shops SET connection_status = 'auth_expired' WHERE company_id = %s AND shop_id = 'shop-a'", (user.company_id,))
    assert execute_order_recovery(user, action['action_id'])['status'] == 'blocked'
    with get_connection() as connection:
        assert connection.execute('SELECT COUNT(*) AS count FROM merchant.order_repair_receipts WHERE action_id = %s', (action['action_id'],)).fetchone()['count'] == 0


def test_persisted_approval_can_execute_after_process_restart(action_runtime: dict[str, str]) -> None:
    user = authenticate(action_runtime["token_a"])
    case_id, _, _ = create_missing_order_case(user, "O-RESTART")
    proposed = build_order_action_plan(user, case_id)
    with get_connection() as connection:
        connection.execute("INSERT INTO support.action_decisions (decision_id, action_id, decision, decided_by) VALUES (%s, %s, 'approved', %s)", (str(uuid4()), proposed["action_id"], user.user_id))
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
    user = authenticate(action_runtime["token_a"])
    with get_connection() as connection:
        connection.execute("UPDATE merchant.shops SET sync_enabled = FALSE, version = version + 1 WHERE company_id = %s AND shop_id = 'shop-a'", (user.company_id,))
    case_id, _, _ = create_missing_order_case(user, "O-ENABLE-SYNC")
    with pytest.raises(ValueError, match="explicit plan"):
        build_order_action_plan(user, case_id)
    proposed = build_order_action_plan(user, case_id, enable_order_sync=True)
    assert proposed["enable_order_sync"] is True
    result = decide_action_plan(user, proposed["action_id"], "approve")
    assert result["status"] == "verified_resolved"
    with get_connection() as connection:
        shop = connection.execute("SELECT sync_enabled FROM merchant.shops WHERE company_id = %s AND shop_id = 'shop-a'", (user.company_id,)).fetchone()
    assert shop["sync_enabled"] is True


def test_existing_correct_order_is_verified_as_no_action_needed(action_runtime: dict[str, str]) -> None:
    user = authenticate(action_runtime["token_a"])
    case_id, event_id, _ = create_missing_order_case(user, "O-ALREADY-DONE")
    event = OrderEvent(event_id=event_id, company_id=user.company_id, shop_id="shop-a", external_order_id="O-ALREADY-DONE", sku="SKU-1", quantity=2, amount_minor=20000, payment_status="paid")
    store_order_event(event)
    assert process_next_task()["status"] == "completed"
    result = build_order_action_plan(user, case_id)
    assert result["status"] == "no_action_needed"
    with get_connection() as connection:
        proposal_count = connection.execute("SELECT COUNT(*) AS count FROM support.action_proposals WHERE case_id = %s", (case_id,)).fetchone()["count"]
    assert proposal_count == 0


def test_order_repair_response_lost_is_reconciled_after_resume(action_runtime: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    user = authenticate(action_runtime["token_a"])
    case_id, _, _ = create_missing_order_case(user, "O-ORDER-RESPONSE-LOST")
    proposed = build_order_action_plan(user, case_id)

    def commit_then_lose(payload: dict[str, object]) -> dict[str, object]:
        receive_order_repair(OrderRepairRequest(**payload), "test-service-token")
        raise httpx.ReadTimeout("response lost", request=httpx.Request("POST", "http://merchant/repairs/orders"))

    monkeypatch.setattr("backend.app.support_action_execution.submit_order_repair", commit_then_lose)
    unknown = decide_action_plan(user, proposed["action_id"], "approve")
    assert unknown["status"] == "executing"
    assert unknown["execution"]["status"] == "unknown"

    def receipt_from_merchant(action: dict[str, object]) -> dict[str, object]:
        with get_connection() as connection:
            row = connection.execute("""SELECT r.receipt_id::text, t.task_id::text, t.status AS task_status
                FROM merchant.order_repair_receipts r JOIN merchant.order_recovery_tasks t ON t.receipt_id = r.receipt_id
                WHERE r.action_id = %s""", (action["action_id"],)).fetchone()
        return {"accepted": True, "duplicate": True, **dict(row)}

    monkeypatch.setattr("backend.app.support_action_execution.get_existing_recovery_receipt", receipt_from_merchant)
    completed = execute_order_recovery(user, proposed["action_id"])
    assert completed["status"] == "verified_resolved"
    with get_connection() as connection:
        receipt_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.order_repair_receipts WHERE action_id = %s", (proposed["action_id"],)).fetchone()["count"]
        order_count = connection.execute("SELECT COUNT(*) AS count FROM merchant.orders WHERE company_id = %s AND external_order_id = 'O-ORDER-RESPONSE-LOST'", (user.company_id,)).fetchone()["count"]
    assert (receipt_count, order_count) == (1, 1)
