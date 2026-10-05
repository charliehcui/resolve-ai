import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from backend.app.auth import authenticate
from backend.app.conversations import process_conversation_message
from backend.app.database import create_conversation, get_connection
from backend.app.handoff import create_support_handoff
from backend.app.models import UserContext
from backend.app.support_action_approvals import authorize_action_decision, decide_action_plan, execute_action_plan, explicit_confirmation, get_action_details, respond_to_action_plan
from backend.app.support_action_plans import create_action_plan
from backend.app.support_action_registry import ACTION_REGISTRY
from backend.app.support_action_verification import verify_background_action, verify_order_recovery, verify_shipment_recovery
from backend.app.support_diagnosis import CandidateAction
from backend.app.support_evidence import load_evidence
from simulator.lab import bootstrap, scenarios
from simulator.services import common, merchant, platform, warehouse, worker


@pytest.fixture()
def scenario_runtime(seeded_database, monkeypatch, tmp_path):
    """Real simulator APIs and PostgreSQL; only transport and model responses are controlled."""
    token_file = tmp_path / "tokens.json"
    service_file = tmp_path / "services.json"
    monkeypatch.setattr(bootstrap, "TOKEN_FILE", token_file)
    monkeypatch.setattr(bootstrap, "SERVICE_TOKEN_FILE", service_file)
    monkeypatch.setattr(scenarios, "USER_TOKEN_FILE", token_file)
    bootstrap.bootstrap()
    tokens = json.loads(token_file.read_text(encoding="utf-8"))
    for module in (common, scenarios, merchant, platform, warehouse, worker):
        monkeypatch.setattr(module, "read_service_token", lambda name: "service-test-only", raising=False)
    monkeypatch.setattr("backend.app.support_tools.read_service_token", lambda name: "service-test-only")
    monkeypatch.setattr("backend.app.support_action_execution.read_service_token", lambda name: "service-test-only")
    monkeypatch.setenv("MERCHANT_URL", "http://merchant")
    monkeypatch.setenv("PLATFORM_URL", "http://platform")
    monkeypatch.setenv("WAREHOUSE_URL", "http://warehouse")
    monkeypatch.setenv("MERCHANT_INGEST_URL", "http://merchant/events/orders")
    monkeypatch.setenv("MERCHANT_SHIPMENT_URL", "http://merchant/events/shipments")
    clients = {"merchant": TestClient(merchant.app), "platform": TestClient(platform.app), "warehouse": TestClient(warehouse.app)}

    def request(method, url, **kwargs):
        parts = urlsplit(url)
        kwargs.pop("timeout", None)
        response = clients[parts.hostname].request(method, parts.path, **kwargs)
        if method == "POST" and parts.path == "/orders" and parts.hostname == "platform" and response.is_success:
            worker.process_next_task()
            worker.process_next_dispatch_task()
        elif method == "POST" and parts.path == "/shipments" and parts.hostname == "warehouse" and response.is_success:
            worker.process_next_shipment_task()
        elif method == "POST" and parts.path == "/stocks/publish" and response.is_success:
            worker.process_next_stock_task()
        return response

    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: request("GET", url, **kwargs))
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: request("POST", url, **kwargs))

    def complete_order(user, action_id, **kwargs):
        worker.process_next_recovery_task()
        return verify_order_recovery(user, action_id, final_check=True)

    def complete_shipment(user, action_id, **kwargs):
        worker.process_next_shipment_recovery_task()
        return verify_shipment_recovery(user, action_id)

    def complete_background(user, action_id, **kwargs):
        worker.process_next_task()
        worker.process_next_stock_task()
        return verify_background_action(user, action_id)

    monkeypatch.setattr("backend.app.support_action_verification.wait_for_order_verification", complete_order)
    monkeypatch.setattr("backend.app.support_action_verification.wait_for_shipment_verification", complete_shipment)
    monkeypatch.setattr("backend.app.support_action_verification.wait_for_background_verification", complete_background)
    return authenticate(tokens["staff-a"]), authenticate(tokens["admin-a"])


def scripted_model(monkeypatch, scenario, calls):
    actions = {"order_sync_failure": "retry_order_sync", "shipment_sync_failure": "resend_shipment", "inventory_mismatch": "refresh_inventory", "worker_task_stuck": "retry_failed_task", "shop_authorization_expired": "request_reauthorization"}

    def model(messages, terminal_only=False, tools=None):
        calls.append(messages)
        text = messages[-1].content
        records = json.loads(text.split("Evidence:\n", 1)[1].split("\n\nRemaining tool budget:", 1)[0])
        shop_id = scenario["shop_id"]
        order_id = scenario.get("order_id")
        if not records:
            if scenario["scenario"] == "inventory_mismatch":
                tool_calls = [{"name": "GetStockStatus", "args": {"shop_id": shop_id, "sku": "SKU-1"}, "id": "read-stock"}]
            elif scenario["scenario"] == "shipment_sync_failure":
                tool_calls = [{"name": name, "args": {"shop_id": shop_id, "order_id": order_id}, "id": name} for name in ("GetWarehouseShipment", "GetShipmentProcessRecords", "GetPlatformShipment")]
            else:
                names = ("GetOrder", "GetOrderProcessRecords")
                if scenario["scenario"] == "worker_task_stuck":
                    names += ("GetWorkerTask",)
                tool_calls = [{"name": name, "args": {"shop_id": shop_id, "order_id": order_id}, "id": name} for name in names]
            tool_calls.append({"name": "GetShopConnectionStatus", "args": {"shop_id": shop_id}, "id": "read-connection"})
            return AIMessage(content="", tool_calls=tool_calls)
        evidence_ids = [item["evidence_id"] for item in records]
        if scenario["scenario"] == "missing_sku_mapping":
            data = {"next_step": "human_support", "human_support": {"reason": "No trusted SKU mapping exists", "known_facts": [{"text": "Order processing is blocked", "evidence_ids": evidence_ids}]}}
        else:
            action = actions.get(scenario["scenario"])
            candidate = {"action_type": action, "reason": "Supported by the observed backend facts", "evidence_ids": evidence_ids} if action else None
            outcome = "retry_later" if scenario["scenario"] in {"rate_limit", "third_party_outage"} else "diagnosed"
            data = {"next_step": "finish", "investigation_complete": {"summary": "Backend investigation completed", "confirmed_facts": [{"text": "Current backend state was read", "evidence_ids": evidence_ids}], "recommended_action": candidate, "outcome": outcome}}
        return AIMessage(content=json.dumps(data))

    monkeypatch.setattr("backend.app.support_diagnosis.call_support_model", model)


def investigate(user, scenario):
    conversation_id = create_conversation(user.company_id, user.user_id)
    create_support_handoff(user, conversation_id, scenario["message"], [])
    return conversation_id, process_conversation_message(user, scenario["message"], conversation_id)


@pytest.mark.parametrize("name", ["order_sync_failure", "shipment_sync_failure", "inventory_mismatch", "worker_task_stuck"])
def test_agent_recommendation_user_confirmation_execution_and_verification(scenario_runtime, monkeypatch, name):
    user, admin = scenario_runtime
    scenario = scenarios.seed_scenario(name)
    calls = []
    scripted_model(monkeypatch, scenario, calls)
    conversation_id, diagnosis = investigate(user, scenario)
    plan = diagnosis["action_plan"]
    assert diagnosis["status"] == "awaiting_confirmation"
    assert plan["risk_level"] == "low" and plan["approval_requirement"] == "user_confirmation"
    assert plan["execution"] is None
    assert all(record.observed_at is not None for record in load_evidence(plan["case_id"]))
    assert len(calls) == 1  # Known identifiers select reads directly, then one diagnosis.
    vague = process_conversation_message(user, "sounds reasonable", conversation_id)
    assert vague["action_plan"]["status"] == "proposed"
    confirmed = process_conversation_message(user, "确认执行", conversation_id)
    assert confirmed["status"] == "verified_resolved"
    assert confirmed["action_plan"]["verification"]["status"] == "verified_resolved"
    assert len(calls) == 1  # No model calls for approval, execution or verification.
    duplicate = decide_action_plan(user, plan["action_id"], "approve")
    assert duplicate["status"] == "verified_resolved"
    assert decide_action_plan(admin, plan["action_id"], "approve")["status"] == "verified_resolved"
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM support.action_executions WHERE action_id = %s", (plan["action_id"],)).fetchone()["count"] == 1
        assert connection.execute("SELECT decided_by FROM support.action_decisions WHERE action_id = %s", (plan["action_id"],)).fetchone()["decided_by"] == user.user_id
        approvals = connection.execute("SELECT status, details FROM support.action_steps WHERE action_id = %s AND step_name = 'human_approval'", (plan["action_id"],)).fetchall()
        assert len(approvals) == 1
        assert approvals[0]["details"]["decided_by"] == user.user_id
        assert approvals[0]["details"]["risk_level"] == "low"
    if name == "inventory_mismatch":
        facts = scenarios.stock_facts(scenario["shop_id"], "SKU-1", "MERCHANT-SKU-DEMO-inventory")
        assert facts["platform"]["quantity"] == 65
        assert facts["platform"]["source_version"] == facts["warehouse"]["version"]


@pytest.mark.parametrize("name,expected", [("shop_authorization_expired", "user_action_required"), ("third_party_outage", "retry_later"), ("rate_limit", "retry_later"), ("missing_sku_mapping", "pending_human")])
def test_user_action_wait_and_human_outcomes_make_no_repair(scenario_runtime, monkeypatch, name, expected):
    user, _ = scenario_runtime
    scenario = scenarios.seed_scenario(name)
    scripted_model(monkeypatch, scenario, [])
    _, result = investigate(user, scenario)
    assert result["status"] == expected
    if name == "missing_sku_mapping":
        assert result["ticket_id"]
    if name == "shop_authorization_expired":
        assert result["action_plan"]["approval_requirement"] == "user_action"
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM support.action_executions").fetchone()["count"] == 0
        if name != "missing_sku_mapping":
            assert connection.execute("SELECT connection_status FROM merchant.shops WHERE shop_id = %s", (scenario["shop_id"],)).fetchone()["connection_status"] != "authorized"


def test_model_cannot_assign_permissions_or_forge_evidence(scenario_runtime):
    user, _ = scenario_runtime
    scenario = scenarios.seed_scenario("order_sync_failure")
    conversation_id = create_conversation(user.company_id, user.user_id)
    _, case_id = create_support_handoff(user, conversation_id, scenario["message"], [])
    with pytest.raises(ValidationError):
        CandidateAction(action_type="retry_order_sync", reason="unsafe", evidence_ids=[], risk_level="low", approval_requirement="none")
    with pytest.raises(ValueError, match="actual Evidence"):
        create_action_plan(user, case_id, CandidateAction(action_type="retry_order_sync", reason="forged", evidence_ids=[str(uuid4())]))
    with pytest.raises(ValueError, match="Unsupported"):
        create_action_plan(user, case_id, CandidateAction(action_type="delete_account", reason="unsupported", evidence_ids=[]))


@pytest.mark.parametrize("risk", ["medium", "high"])
def test_privileged_risk_policy_requires_admin_without_fake_backend(scenario_runtime, monkeypatch, risk):
    user, admin = scenario_runtime
    # Exercise the policy gate using an existing demonstrable operation.
    monkeypatch.setitem(ACTION_REGISTRY, "retry_order_sync", {**ACTION_REGISTRY["retry_order_sync"], "risk_level": risk, "approval_requirement": "admin"})
    scenario = scenarios.seed_scenario("order_sync_failure")
    scripted_model(monkeypatch, scenario, [])
    conversation_id, result = investigate(user, scenario)
    action_id = result["action_plan_id"]
    with pytest.raises(PermissionError, match="admin"):
        decide_action_plan(user, action_id, "approve")
    pending = process_conversation_message(user, "确认执行", conversation_id)
    assert pending["status"] == "proposed"
    assert "管理员" in pending["answer"]
    assert pending["action_plan"]["decision"] is None
    assert pending["action_plan"]["execution"] is None
    assert decide_action_plan(admin, action_id, "approve")["status"] == "verified_resolved"


@pytest.mark.parametrize("name", ["inventory_mismatch", "worker_task_stuck"])
def test_changed_source_blocks_new_actions(scenario_runtime, monkeypatch, name):
    user, _ = scenario_runtime
    scenario = scenarios.seed_scenario(name)
    scripted_model(monkeypatch, scenario, [])
    _, result = investigate(user, scenario)
    with get_connection() as connection:
        if name == "inventory_mismatch":
            connection.execute("UPDATE warehouse.stock_items SET version = version + 1, physical_quantity = physical_quantity + 1")
        else:
            connection.execute("UPDATE merchant.order_tasks SET version = version + 1 WHERE external_order_id = %s", (scenario["order_id"],))
    assert decide_action_plan(user, result["action_plan_id"], "approve")["status"] == "blocked"


def test_post_receipt_waits_for_real_readback(scenario_runtime, monkeypatch):
    user, _ = scenario_runtime
    scenario = scenarios.seed_scenario("inventory_mismatch")
    scripted_model(monkeypatch, scenario, [])
    _, result = investigate(user, scenario)
    monkeypatch.setattr("backend.app.support_action_verification.wait_for_background_verification", verify_background_action)
    pending = decide_action_plan(user, result["action_plan_id"], "approve")
    assert pending["execution"]["receipt"]["accepted"] is True
    assert pending["status"] == "awaiting_verification" and pending["verification"]["status"] == "pending"
    worker.process_next_stock_task()
    assert verify_background_action(user, result["action_plan_id"])["status"] == "verified_resolved"


def test_same_company_other_user_cannot_confirm_plan(scenario_runtime, monkeypatch):
    user, _ = scenario_runtime
    scenario = scenarios.seed_scenario("order_sync_failure")
    scripted_model(monkeypatch, scenario, [])
    _, result = investigate(user, scenario)
    other = UserContext(company_id=user.company_id, user_id="other-user", role="staff")
    with pytest.raises(PermissionError, match="scope"):
        decide_action_plan(other, result["action_plan_id"], "approve")


@pytest.mark.parametrize("text", ["maybe", "ok", "looks good", "sounds reasonable", "可以考虑", "稍后确认", "yes but wait", "确认执行另一个订单"])
def test_vague_text_is_not_approval(text):
    assert explicit_confirmation(text) is None


def test_expired_plan_does_not_execute(scenario_runtime, monkeypatch):
    user, _ = scenario_runtime
    scenario = scenarios.seed_scenario("worker_task_stuck")
    scripted_model(monkeypatch, scenario, [])
    _, result = investigate(user, scenario)
    with get_connection() as connection:
        connection.execute("UPDATE support.action_proposals SET expires_at = %s WHERE action_id = %s", (datetime.now(UTC) - timedelta(seconds=1), result["action_plan_id"]))
    assert decide_action_plan(user, result["action_plan_id"], "approve")["status"] == "expired"


def test_low_risk_does_not_allow_agent_identity(scenario_runtime):
    user, _ = scenario_runtime
    agent = UserContext(company_id=user.company_id, user_id="agent", role="agent")
    with pytest.raises(PermissionError):
        authorize_action_decision(agent, {"action_type": "retry_order_sync"})


@pytest.mark.parametrize("name", ["inventory_mismatch", "worker_task_stuck"])
def test_lost_repair_response_is_reconciled_without_second_effect(scenario_runtime, monkeypatch, name):
    from backend.app import support_action_execution

    user, _ = scenario_runtime
    scenario = scenarios.seed_scenario(name)
    scripted_model(monkeypatch, scenario, [])
    _, result = investigate(user, scenario)
    submit = support_action_execution.submit_background_repair
    writes = []

    def commit_then_timeout(resource, payload):
        writes.append(payload["request_id"])
        submit(resource, payload)
        raise httpx.ReadTimeout("Accepted but response lost")

    monkeypatch.setattr(support_action_execution, "submit_background_repair", commit_then_timeout)
    unknown = decide_action_plan(user, result["action_plan_id"], "approve")
    assert unknown["execution"]["status"] == "unknown"
    assert execute_action_plan(user, result["action_plan_id"])["status"] == "verified_resolved"
    assert len(writes) == 1
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM merchant.action_repair_receipts WHERE action_id = %s", (result["action_plan_id"],)).fetchone()["count"] == 1


def test_expired_plan_can_be_rebuilt_after_fresh_investigation(scenario_runtime, monkeypatch):
    user, _ = scenario_runtime
    scenario = scenarios.seed_scenario("inventory_mismatch")
    calls = []
    scripted_model(monkeypatch, scenario, calls)
    conversation_id, first = investigate(user, scenario)
    with get_connection() as connection:
        connection.execute("UPDATE support.action_proposals SET expires_at = %s WHERE action_id = %s", (datetime.now(UTC) - timedelta(seconds=1), first["action_plan_id"]))
    second = process_conversation_message(user, "重新检查", conversation_id)
    assert second["status"] == "awaiting_confirmation"
    assert second["action_plan_id"] != first["action_plan_id"]
    assert len(calls) == 2  # Each fresh investigation needs one diagnosis.
    with get_connection() as connection:
        assert connection.execute("SELECT status FROM support.action_proposals WHERE action_id = %s", (first["action_plan_id"],)).fetchone()["status"] == "expired"


def test_reauthorization_recheck_reads_fresh_facts_and_retains_old_evidence(scenario_runtime, monkeypatch):
    user, _ = scenario_runtime
    scenario = scenarios.seed_scenario("shop_authorization_expired")
    scripted_model(monkeypatch, scenario, [])
    conversation_id, first = investigate(user, scenario)
    assert first["status"] == "user_action_required"
    scenarios.restore_connection(scenario["shop_id"])
    scripted_model(monkeypatch, {**scenario, "scenario": "healthy_connection"}, [])
    second = process_conversation_message(user, "重新检查连接", conversation_id)
    assert second["status"] == "diagnosed"
    connections = [record.response["connection_status"] for record in load_evidence(first["case_id"]) if record.tool_name == "GetShopConnectionStatus"]
    assert connections[0] == "auth_expired" and connections[-1] == "authorized"


def test_unapproved_plan_never_dispatches_to_executor(scenario_runtime, monkeypatch):
    user, _ = scenario_runtime
    scenario = scenarios.seed_scenario("order_sync_failure")
    scripted_model(monkeypatch, scenario, [])
    _, result = investigate(user, scenario)
    writes = []
    monkeypatch.setattr("backend.app.support_action_execution.execute_order_recovery", lambda *args: writes.append(args))
    with pytest.raises(ValueError, match="not approved"):
        execute_action_plan(user, result["action_plan_id"])
    assert writes == []
    assert get_action_details(user, result["action_plan_id"])["decision"] is None


def test_chat_confirmation_cannot_use_another_conversations_plan(scenario_runtime, monkeypatch):
    user, _ = scenario_runtime
    scenario = scenarios.seed_scenario("worker_task_stuck")
    scripted_model(monkeypatch, scenario, [])
    _, result = investigate(user, scenario)
    another_conversation = create_conversation(user.company_id, user.user_id)
    history = [{"role": "assistant", "metadata": {"action_plan_id": result["action_plan_id"]}}]
    with pytest.raises(PermissionError, match="conversation"):
        respond_to_action_plan(user, another_conversation, "yes", history)
    details = get_action_details(user, result["action_plan_id"])
    assert details["decision"] is None and details["execution"] is None


@pytest.mark.parametrize("latest", [{"role": "user"}, {"role": "assistant", "metadata": {}}])
def test_chat_confirmation_does_not_reuse_a_plan_from_older_messages(latest):
    user = UserContext(company_id="company-a", user_id="staff-a", role="staff")
    history = [{"role": "assistant", "metadata": {"action_plan_id": str(uuid4())}}, latest]
    assert respond_to_action_plan(user, str(uuid4()), "yes", history) is None


def test_rejected_plan_cannot_be_approved_again(scenario_runtime, monkeypatch):
    user, admin = scenario_runtime
    scenario = scenarios.seed_scenario("worker_task_stuck")
    scripted_model(monkeypatch, scenario, [])
    conversation_id, result = investigate(user, scenario)
    rejected = process_conversation_message(user, "取消", conversation_id)
    assert rejected["status"] == "rejected"
    repeated = decide_action_plan(admin, result["action_plan_id"], "approve")
    assert repeated["status"] == "rejected" and repeated["execution"] is None
    assert repeated["decision"]["decision"] == "rejected"
    assert repeated["decision"]["decided_by"] == user.user_id
    assert len([step for step in repeated["steps"] if step["step_name"] == "human_approval"]) == 1


def test_approved_but_unsubmitted_expired_plan_does_not_execute(scenario_runtime, monkeypatch):
    user, _ = scenario_runtime
    scenario = scenarios.seed_scenario("order_sync_failure")
    scripted_model(monkeypatch, scenario, [])
    _, result = investigate(user, scenario)
    writes = []

    def defer_execution(actor, action_id):
        writes.append(action_id)
        return get_action_details(actor, action_id)

    monkeypatch.setattr("backend.app.support_action_approvals.execute_action_plan", defer_execution)
    assert decide_action_plan(user, result["action_plan_id"], "approve")["status"] == "approved"
    with get_connection() as connection:
        connection.execute("UPDATE support.action_proposals SET expires_at = %s WHERE action_id = %s", (datetime.now(UTC) - timedelta(seconds=1), result["action_plan_id"]))
    expired = decide_action_plan(user, result["action_plan_id"], "approve")
    assert expired["status"] == "expired" and len(writes) == 1
    assert expired["decision"]["decided_by"] == user.user_id
    steps = [step for step in expired["steps"] if step["step_name"] == "human_approval"]
    assert [step["status"] for step in steps] == ["approved", "expired"]
    assert "decided_by" not in steps[-1]["details"]


@pytest.mark.parametrize("setting", ["enable_order_sync", "enable_shipment_sync"])
def test_shop_wide_changes_require_admin(setting):
    user = UserContext(company_id="company-a", user_id="staff-a", role="staff")
    action = {"action_type": "retry_order_sync", setting: True, "risk_level": "low", "approval_requirement": "user_confirmation"}
    with pytest.raises(PermissionError, match="admin"):
        authorize_action_decision(user, action)
    authorize_action_decision(UserContext(company_id=user.company_id, user_id="admin-a", role="admin"), action)


def test_reauthorization_cannot_be_approved_as_backend_write():
    user = UserContext(company_id="company-a", user_id="admin-a", role="admin")
    with pytest.raises(ValueError, match="outside the Agent"):
        authorize_action_decision(user, {"action_type": "request_reauthorization"})
