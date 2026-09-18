from typing import Self
from uuid import uuid4

import pytest

from backend.app.auth import authenticate
from backend.app.database import create_conversation, save_message
from backend.app.handoff import handoff_to_support
from backend.app.support_cases import update_case
from backend.app.support_evidence import EvidenceRecord, save_evidence
from backend.app.support_workflow import run_support_graph
from backend.app.tickets import create_ticket, list_engineer_tickets, recheck_ticket, show_ticket


def unresolved_case(token: str) -> tuple[object, str, str]:
    auth = authenticate(token)
    conversation_id = create_conversation(auth.company_id, auth.user_id)
    save_message(conversation_id, "user", "shop-a order O-800 did not sync")
    _, case_id = handoff_to_support(auth, conversation_id, "shop-a order O-800 did not sync", "I could not resolve it from product documentation.", [])
    update_case(case_id, "pending_human", "Evidence is insufficient", 2, 20)
    return auth, conversation_id, case_id


def tool_evidence(name: str, response: dict[str, object], status: str = "success", sequence: int = 1) -> EvidenceRecord:
    return EvidenceRecord(evidence_id=str(uuid4()), sequence=sequence, batch_id=str(uuid4()), parallel=True, tool_name=name, request={}, response=response, source_service="test", status=status, latency_ms=1)


def test_ticket_captures_grounded_investigation_and_deduplicates(seeded_database: dict[str, str]) -> None:
    auth, conversation_id, case_id = unresolved_case(seeded_database["token_a"])
    success = save_evidence(case_id, "company-a", str(uuid4()), False, None, "GetShopStatus", {"shop_id": "shop-a"}, {"sync_enabled": True, "version": 3}, "merchant", "shop-a", "success", 2, None)
    failure = save_evidence(case_id, "company-a", str(uuid4()), False, None, "CheckConnection", {"shop_id": "shop-a"}, {"error_code": "UPSTREAM_TIMEOUT"}, "merchant", "shop-a", "unavailable", 3000, None)

    first = create_ticket(auth, conversation_id, "evidence_insufficient", "Investigation cannot confirm the cause")
    second = create_ticket(auth, conversation_id, "support_unresolved", "Repeated escalation")

    assert first["ticket_id"] == second["ticket_id"]
    assert second["duplicate"] is True
    assert first["assigned_to"] == "engineer-a"
    assert first["business_target"] == {"shop_id": "shop-a", "order_id": "O-800", "sku": None}
    assert first["failed_evidence_ids"] == [failure.evidence_id]
    assert any(item["evidence_id"] == success.evidence_id for item in first["confirmed_facts"])
    assert first["possible_causes"] == [{"evidence_id": failure.evidence_id, "error_code": "UPSTREAM_TIMEOUT"}]
    assert any(item["reason"] == "ORDER_SYNC_DISABLED" for item in first["excluded_causes"])
    assert first["unknowns"]
    assert first["next_steps"]


def test_ticket_policy_and_explicit_human_request(seeded_database: dict[str, str]) -> None:
    auth = authenticate(seeded_database["token_a"])
    conversation_id = create_conversation(auth.company_id, auth.user_id)
    with pytest.raises(ValueError, match="unresolved"):
        create_ticket(auth, conversation_id, "support_unresolved", "No unresolved work exists")
    ticket = create_ticket(auth, conversation_id, "user_requested", "Please let me speak to an engineer")
    assert ticket["category"] == "general"
    assert ticket["handoff_id"] is None
    assert ticket["trigger"] == "user_requested"


def test_engineer_reads_only_explicitly_granted_tickets(seeded_database: dict[str, str]) -> None:
    merchant, conversation_id, _ = unresolved_case(seeded_database["token_a"])
    ticket = create_ticket(merchant, conversation_id, "support_unresolved", "Needs engineering")
    engineer_a = authenticate(seeded_database["token_engineer_a"])
    engineer_b = authenticate(seeded_database["token_engineer_b"])

    assert [item["ticket_id"] for item in list_engineer_tickets(engineer_a)] == [ticket["ticket_id"]]
    assert show_ticket(engineer_a, ticket["ticket_id"])["company_id"] == "company-a"
    assert list_engineer_tickets(engineer_b) == []
    with pytest.raises(PermissionError):
        show_ticket(engineer_b, ticket["ticket_id"])


def test_no_assignment_rule_creates_unassigned_queue_item(seeded_database: dict[str, str]) -> None:
    merchant = authenticate(seeded_database["token_b"])
    conversation_id = create_conversation(merchant.company_id, merchant.user_id)
    ticket = create_ticket(merchant, conversation_id, "user_requested", "Human support please")
    assert ticket["assigned_to"] is None
    assert list_engineer_tickets(authenticate(seeded_database["token_engineer_b"])) == []


def test_other_merchant_cannot_read_ticket_in_same_company(seeded_database: dict[str, str]) -> None:
    merchant, conversation_id, _ = unresolved_case(seeded_database["token_a"])
    ticket = create_ticket(merchant, conversation_id, "support_unresolved", "Needs engineering")
    with pytest.raises(PermissionError):
        show_ticket(authenticate(seeded_database["token_staff_a"]), ticket["ticket_id"])


def test_support_safe_stop_creates_ticket_without_model_write_access(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    auth, conversation_id, _ = unresolved_case(seeded_database["token_a"])

    class FakeSaver:
        @classmethod
        def from_conn_string(cls, _: str) -> "FakeSaver":
            return cls()

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def setup(self) -> None:
            return None

    class FakeCompiled:
        def invoke(self, *_: object, **__: object) -> dict[str, object]:
            return {"answer": "Investigation stopped with unresolved evidence.", "status": "pending_human", "evidence": [], "models_used": [], "usage": {}}

    class FakeGraph:
        def compile(self, **_: object) -> FakeCompiled:
            return FakeCompiled()

    monkeypatch.setattr("backend.app.support_workflow.PostgresSaver", FakeSaver)
    monkeypatch.setattr("backend.app.support_workflow.build_support_investigation_graph", lambda: FakeGraph())
    result = run_support_graph("continue", auth, conversation_id)
    assert result.status == "pending_human"
    assert result.ticket_id is not None
    assert show_ticket(auth, result.ticket_id)["trigger"] == "support_unresolved"


def test_order_ticket_recheck_closes_and_failed_recheck_reopens(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    merchant, conversation_id, _ = unresolved_case(seeded_database["token_a"])
    ticket = create_ticket(merchant, conversation_id, "support_unresolved", "Needs engineering")
    engineer = authenticate(seeded_database["token_engineer_a"])
    platform = {"event_id": "event-1", "sku": "SKU-1", "quantity": 2, "amount_minor": 2000}
    merchant_order = {"event_id": "event-1", "platform_sku": "SKU-1", "quantity": 2, "source_quantity": 2, "amount_minor": 2000, "source_amount_minor": 2000, "merchant_order_count": 1, "task_status": "completed"}
    monkeypatch.setattr("backend.app.tickets.execute_tool_batch", lambda *_args, **_kwargs: [tool_evidence("GetOrder", platform), tool_evidence("GetProcessRecords", merchant_order, sequence=2)])

    closed = recheck_ticket(engineer, ticket["ticket_id"])
    assert closed["status"] == "closed" and closed["recheck_status"] == "RESOLVED"

    merchant_order["task_status"] = "blocked"
    reopened = recheck_ticket(engineer, ticket["ticket_id"])
    assert reopened["status"] == "open" and reopened["recheck_status"] == "UNRESOLVED"
    assert [item["status"] for item in reopened["rechecks"]] == ["RESOLVED", "UNRESOLVED"]


def test_shipment_ticket_recheck_closes_only_when_three_systems_match(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    merchant, conversation_id, case_id = unresolved_case(seeded_database["token_a"])
    save_evidence(case_id, "company-a", str(uuid4()), False, None, "GetShipment", {"shop_id": "shop-a", "order_id": "O-800"}, {"shipment_id": "shipment-1"}, "warehouse", "shipment-1", "success", 1, None)
    ticket = create_ticket(merchant, conversation_id, "support_unresolved", "Shipment needs engineering")
    shipment = {"shipment_id": "shipment-1", "carrier": "test-express", "tracking_number": "TRACK-1", "shipment_count": 1}
    merchant_shipment = {**shipment, "task_status": "completed"}
    platform_shipment = {key: shipment[key] for key in ("shipment_id", "carrier", "tracking_number")}
    monkeypatch.setattr("backend.app.tickets.execute_tool_batch", lambda *_args, **_kwargs: [tool_evidence("GetShipment", shipment), tool_evidence("GetShipmentRecords", merchant_shipment, sequence=2), tool_evidence("GetPlatformShipment", platform_shipment, sequence=3)])

    result = recheck_ticket(authenticate(seeded_database["token_engineer_a"]), ticket["ticket_id"])
    assert result["category"] == "shipment" and result["status"] == "closed"
    assert result["recheck_status"] == "RESOLVED"


def test_stock_ticket_recheck_uses_existing_stock_verification(seeded_database: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    merchant = authenticate(seeded_database["token_a"])
    conversation_id = create_conversation(merchant.company_id, merchant.user_id)
    _, case_id = handoff_to_support(merchant, conversation_id, "shop-a 的 SKU-1 库存不同", "需要后台调查", [])
    update_case(case_id, "pending_human", "Stock evidence needs engineering", 1, 1)
    ticket = create_ticket(merchant, conversation_id, "support_unresolved", "Stock needs engineering")
    stock = {"merchant": {"empty": False, "rule": {"safety_stock": 5}}, "warehouse": {"physical_quantity": 80, "reserved_quantity": 10, "version": 1, "updated_at": "2026-09-18T00:00:00+00:00"}, "platform": {"quantity": 65, "source_version": 1}, "assessment": "consistent"}
    monkeypatch.setattr("backend.app.tickets.execute_tool_batch", lambda *_args, **_kwargs: [tool_evidence("GetStockFacts", stock)])

    result = recheck_ticket(authenticate(seeded_database["token_engineer_a"]), ticket["ticket_id"])
    assert result["category"] == "stock" and result["status"] == "closed"
    assert result["recheck_status"] == "RESOLVED"


def test_ticket_without_business_identifier_stays_open_as_needs_info(seeded_database: dict[str, str]) -> None:
    merchant = authenticate(seeded_database["token_a"])
    conversation_id = create_conversation(merchant.company_id, merchant.user_id)
    ticket = create_ticket(merchant, conversation_id, "user_requested", "Please assign an engineer")

    result = recheck_ticket(authenticate(seeded_database["token_engineer_a"]), ticket["ticket_id"])
    assert result["status"] == "open" and result["recheck_status"] == "NEEDS_INFO"
    assert "business_target" in result["rechecks"][-1]["details"]["missing"]
