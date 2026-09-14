import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import customer_workflow, main, resolvelab, support_agent, support_sessions, support_workflow, tickets
from app.customer_agent import CustomerResolution, CustomerVerification, ProblemDetails
from app.customer_document_ingestion import load_customer_document
from app.db.database import Base
from app.db.models import SupportSession, Ticket
from app.handoff import SupportHandoffSummary
from app.support_results import SupportInvestigationResult
from simulator.app import app as simulator_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = json.loads((PROJECT_ROOT / "simulator/scenarios/v1.json").read_text(encoding="utf-8"))["scenarios"]
CASES = json.loads((PROJECT_ROOT / "evals/datasets/support_cases_v1.json").read_text(encoding="utf-8"))["cases"]


class ScenarioSupportModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "scenario-support-test-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]

        if not tool_messages:
            handoff = json.loads(messages[-1].content.split("Structured handoff:\n", 1)[1])

            if handoff["affected_feature"] == "order notifications":
                calls = [{"name": "get_event_notification_deliveries", "args": {"customer_id": "customer_999"}, "id": "delivery_call"}, {"name": "get_platform_status", "args": {}, "id": "platform_call"}]
            else:
                calls = [{"name": "get_background_operation", "args": {"customer_id": "customer_999"}, "id": "operation_call"}, {"name": "get_platform_status", "args": {"service": "report_exports"}, "id": "platform_call"}]

            output = AIMessage(content="", tool_calls=calls)
        else:
            data = {message.name: json.loads(message.content) for message in tool_messages}
            platform = data["get_platform_status"]
            operation = data.get("get_background_operation")
            deliveries = data.get("get_event_notification_deliveries")

            if any(isinstance(value, dict) and value.get("status") == "error" for value in data.values()):
                outcome = "engineer_escalation"
                conclusion = "Required internal data is unavailable."
                explanation = "我们暂时无法取得足够的信息，已经交给工程师继续检查。"
                facts = []
            elif operation is not None and operation["status"] != operation["latest_run_status"]:
                outcome = "engineer_escalation"
                conclusion = "The failed operation conflicts with its successful latest run."
                explanation = "目前的信息不一致，已经交给工程师继续检查。"
                facts = [f"Operation status is {operation['status']}; latest run status is {operation['latest_run_status']}."]
            elif operation is not None and operation["failure_code"] == "dependency_timeout" and operation["retry_allowed"] and platform["status"] == "operational":
                outcome = "action_required"
                conclusion = "The export needs a human-controlled internal retry; no action has been executed."
                explanation = "报表导出暂时未完成，需要技术人员进一步处理。目前没有执行任何更改。"
                facts = ["The export failed with dependency_timeout and retry_allowed is true.", "The report_exports platform is operational."]
            elif deliveries and all(delivery["response_status"] == 401 for delivery in deliveries) and platform["status"] == "operational":
                outcome = "resolution"
                conclusion = "The receiving endpoint rejected notifications while the platform was operational."
                explanation = "通知已发出，但接收地址拒绝了请求。请检查接收端的访问设置后再试。"
                facts = ["The latest notification deliveries returned HTTP 401.", "The event_notifications platform is operational."]
            else:
                raise AssertionError("Unexpected simulator state")

            result = SupportInvestigationResult(conclusion=conclusion, supporting_facts=facts, customer_explanation=explanation, outcome=outcome)
            output = AIMessage(content="", tool_calls=[{"name": "SupportInvestigationResult", "args": result.model_dump(), "id": "result_call"}])

        return ChatResult(generations=[ChatGeneration(message=output)])


@pytest.fixture
def scenario_environment(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database_session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    for module in (main, support_sessions, support_workflow, tickets):
        monkeypatch.setattr(module, "SessionLocal", database_session)

    simulator = TestClient(simulator_app)
    urls: list[str] = []

    def read_simulator(url: str, timeout: float):
        assert timeout == 5.0
        assert url.startswith(resolvelab.settings.resolvelab_base_url)
        urls.append(url)
        return simulator.get(url.removeprefix(resolvelab.settings.resolvelab_base_url))

    monkeypatch.setattr(resolvelab.httpx, "get", read_simulator)

    def understand_problem(messages: list[str], current: ProblemDetails | None):
        return ProblemDetails(summary="当前操作无法正常完成。", affected_feature="unknown", problem="The requested operation does not complete.", customer_goal="恢复正常使用。", missing_information=["affected feature"])

    def update_problem(problem: ProblemDetails, data: dict[str, object]):
        feature = data["current_product_context"]["affected_feature"]
        summary = "订单通知收不到。" if feature == "order notifications" else "报表下载不下来。"
        return problem.model_copy(update={"summary": summary, "affected_feature": feature, "missing_information": []})

    class CustomerDocumentSearch:
        def invoke(self, search_input):
            assert search_input["version"] == "2026.8"
            source = "docs/customer/setup.md" if "order notifications" in search_input["customer_question"] else "docs/customer/report-exports.md"
            document = load_customer_document(PROJECT_ROOT / source)
            return [{"chunk_id": f"{source}:0", "source_uri": source, "version": document.metadata["version"], "content": document.page_content}]

    def resolve_customer(problem, data, documents):
        can_resolve = data["current_product_context"]["feature_enabled"] is False
        return CustomerResolution(can_resolve=can_resolve, explanation="订单通知目前没有开启。" if can_resolve else "需要技术支持继续检查。", steps=["打开设置并开启订单通知。", "发送一条测试通知。"] if can_resolve else [], citation_ids=[documents[0]["chunk_id"]] if can_resolve else [], verification_method="customer_confirmation_or_tool")

    monkeypatch.setattr(customer_workflow, "update_customer_problem", understand_problem)
    monkeypatch.setattr(customer_workflow, "update_customer_problem_with_customer_side_data", update_problem)
    monkeypatch.setattr(customer_workflow, "should_get_recent_customer_activity", lambda problem, context: True)
    monkeypatch.setattr(customer_workflow, "create_customer_question", lambda *args: pytest.fail("Do not ask for feature information already available from the server"))
    monkeypatch.setattr(customer_workflow, "retrieve_documents_for_customer_question", CustomerDocumentSearch())
    monkeypatch.setattr(customer_workflow, "create_customer_resolution_from_documents", resolve_customer)
    monkeypatch.setattr(customer_workflow, "create_support_handoff_summary", lambda problem, messages: SupportHandoffSummary(issue_summary=problem.problem, customer_impact="The customer cannot complete the requested operation.", approximate_start_time=None))
    monkeypatch.setattr(customer_workflow, "verify_customer_resolution", lambda problem, resolution, message: CustomerVerification(result="resolved", supporting_text=message))
    agent = create_agent(model=ScenarioSupportModel(), tools=support_agent.support_investigation_tools, system_prompt=support_agent.SUPPORT_INVESTIGATION_SYSTEM_PROMPT, response_format=ToolStrategy(SupportInvestigationResult), middleware=[support_agent.bind_support_tool_customer], context_schema=support_agent.SupportToolContext)
    monkeypatch.setattr(support_agent, "support_investigation_agent", agent)

    with TestClient(main.app) as client:
        yield client, database_session, urls

    simulator.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.mark.parametrize("case", CASES, ids=[case["case_id"] for case in CASES])
def test_representative_paths_use_actual_simulator_and_both_graphs(case, scenario_environment):
    client, database_session, urls = scenario_environment
    scenario = next(scenario for scenario in SCENARIOS if scenario["scenario_id"] == case["scenario_id"])
    customer_id = scenario["account"]["customer_id"]
    response = client.post("/api/v1/support-sessions", json={"customer_id": customer_id, "message": case["customer_message"]})
    assert response.status_code == 201, response.text
    result = response.json()

    if scenario["expected_outcome"] == "resolved":
        assert result["status"] == "waiting_for_verification"
        assert result["ticket_id"] is None
        response = client.post(f"/api/v1/support-sessions/{result['session_id']}/messages", json={"message": scenario["customer_confirmation"]})
        assert response.status_code == 200, response.text
        result = response.json()

    assert result["status"] == case["expected_outcome"]
    assert "tools_used" not in result
    assert "investigation_result" not in result
    assert all("/customers/customer_999" not in url for url in urls)
    assert any("/product-context" in url for url in urls)
    assert any("/recent-activity" in url for url in urls)

    with database_session() as database:
        session = database.get(SupportSession, result["session_id"])
        assert session.status == case["expected_outcome"]

        if result["ticket_id"] is None:
            assert database.scalar(select(func.count()).select_from(Ticket)) == 0
            assert all("/platform-status" not in url and "/background-operation" not in url and "/event-notification-deliveries" not in url for url in urls)
        else:
            ticket_response = client.get(f"/api/v1/tickets/{result['ticket_id']}")
            assert ticket_response.status_code == 200
            ticket = ticket_response.json()
            assert ticket["handoff"]["customer_id"] == customer_id
            assert ticket["handoff"]["remaining_questions"] == []
            assert ticket["investigation_tools"] == scenario["expected_support_tools"]
            assert set(ticket["investigation_tools"]).isdisjoint(scenario["forbidden_tools"])
            assert session.customer_result == result["customer_response"]

            if result["status"] == "action_required":
                assert ticket["status"] == "ACTION_REQUIRED"
                assert "没有执行" in result["customer_response"]

            repeat = client.post(f"/api/v1/support-sessions/{result['session_id']}/messages", json={"message": "请继续。"})
            assert repeat.json()["ticket_id"] == result["ticket_id"]
            assert database.scalar(select(func.count()).select_from(Ticket)) == 1


@pytest.mark.parametrize("failed_path, expected_status", [("/product-context", "engineer_escalation"), ("/recent-activity", "action_required"), ("/background-operation", "engineer_escalation"), ("/platform-status", "engineer_escalation")])
def test_tool_failure_uses_alternative_evidence_or_escalates(failed_path, expected_status, scenario_environment, monkeypatch):
    client, database_session, urls = scenario_environment
    original_get = resolvelab.httpx.get

    def fail_selected_query(url: str, timeout: float):
        if failed_path in url:
            return httpx.Response(503, request=httpx.Request("GET", url), json={"detail": "Service unavailable"})
        return original_get(url, timeout)

    monkeypatch.setattr(resolvelab.httpx, "get", fail_selected_query)
    response = client.post("/api/v1/support-sessions", json={"customer_id": "customer_003", "message": "报表一直下载不下来。"})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == expected_status
    assert "http_error" not in result["customer_response"]
    assert "503" not in result["customer_response"]
    ticket = client.get(f"/api/v1/tickets/{result['ticket_id']}").json()
    assert all(fact["name"] not in ("error", "status") for fact in ticket["handoff"]["collected_facts"])

    if failed_path in ("/product-context", "/recent-activity"):
        assert "tool_errors" in ticket["handoff"]["environment_snapshot"]


def test_versioned_cases_reference_all_four_valid_scenarios():
    assert len(CASES) == 12
    assert len({case["case_id"] for case in CASES}) == 12
    assert {case["scenario_id"] for case in CASES} == {scenario["scenario_id"] for scenario in SCENARIOS}
    assert {scenario["expected_outcome"] for scenario in SCENARIOS} == {"resolved", "support_resolved", "action_required", "engineer_escalation"}
