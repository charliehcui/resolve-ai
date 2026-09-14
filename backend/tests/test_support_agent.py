import pytest
from langchain_core.messages import ToolMessage

from app import support_agent
from app.handoff import SupportHandoff
from app.support_agent import SupportInvestigationRun, support_investigation_tools
from app.support_results import SupportInvestigationResult
from app.tickets import TicketContext, TicketStatus


def test_support_agent_only_has_read_tools() -> None:
    tool_names = []

    for current_tool in support_investigation_tools:
        tool_names.append(current_tool.name)

    assert tool_names == [
        "get_customer_account",
        "get_event_notification_deliveries",
        "get_platform_status",
        "get_background_operation",
    ]


def test_support_prompt_uses_handoff_without_reasking_customer() -> None:
    prompt = support_agent.SUPPORT_INVESTIGATION_SYSTEM_PROMPT

    assert "Read the complete structured handoff" in prompt
    assert "do not ask the customer to repeat them" in prompt
    assert "Every key conclusion" in prompt
    assert "engineer_escalation" in prompt


def test_support_agent_returns_result_and_actual_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    handoff = SupportHandoff(
        support_session_id="session_001",
        customer_id="customer_001",
        issue_summary="Order notifications are not delivered.",
        affected_feature="order notifications",
        customer_impact="The customer cannot receive order status updates.",
        approximate_start_time="This morning",
        environment_snapshot={"product_version": "2026.8"},
        collected_facts=[],
        attempted_steps=["重新保存通知地址。"],
        citation_ids=[],
        remaining_questions=[],
        handoff_reason="The customer-side steps did not resolve the problem.",
    )
    ticket = TicketContext(id=1, support_session_id="session_001", handoff=handoff, status=TicketStatus.OPEN)
    expected_result = SupportInvestigationResult(
        conclusion="The customer endpoint rejected the notifications while the platform remained operational.",
        supporting_facts=["The two latest deliveries returned HTTP 401.", "The platform status is operational."],
        customer_explanation="通知已发送，但接收地址拒绝了请求。请检查接收端的访问设置。",
        outcome="resolution",
    )

    class FakeSupportAgent:
        def invoke(self, agent_input: dict[str, object], config: dict[str, object], *, context: dict[str, object]) -> dict[str, object]:
            assert "Structured handoff" in str(agent_input)
            assert config == {"recursion_limit": 10}
            assert context == {"customer_id": "customer_001"}
            return {
                "messages": [
                    ToolMessage(content='[{"response_status": 401}]', tool_call_id="call_1", name="get_event_notification_deliveries"),
                    ToolMessage(content='{"status": "operational"}', tool_call_id="call_2", name="get_platform_status"),
                    ToolMessage(content="structured result", tool_call_id="call_3", name="SupportInvestigationResult"),
                ],
                "structured_response": expected_result,
            }

    monkeypatch.setattr(support_agent, "support_investigation_agent", FakeSupportAgent())

    investigation = support_agent.investigate_support_ticket(ticket)

    assert investigation == SupportInvestigationRun(
        result=expected_result,
        tools_used=["get_event_notification_deliveries", "get_platform_status"],
    )


@pytest.mark.parametrize("tool_content", ['{"status": "error", "error": {"code": "http_error"}}', "[]", "invalid JSON"])
def test_support_agent_cannot_claim_resolution_without_successful_tool_data(monkeypatch, tool_content):
    handoff = SupportHandoff(support_session_id="session_001", customer_id="customer_001", issue_summary="Notifications are unavailable.", affected_feature="order notifications", customer_impact="The customer cannot receive notifications.", environment_snapshot={}, collected_facts=[], attempted_steps=[], citation_ids=[], remaining_questions=[], handoff_reason="Customer guidance is insufficient.")
    ticket = TicketContext(id=1, support_session_id="session_001", handoff=handoff, status=TicketStatus.OPEN)

    class UnsupportedSupportAgent:
        def invoke(self, agent_input, config, *, context):
            return {"messages": [ToolMessage(content=tool_content, name="get_platform_status", tool_call_id="failed_call")], "structured_response": SupportInvestigationResult(conclusion="Unsupported resolution claim.", supporting_facts=["Invented success."], customer_explanation="问题已经解决。", outcome="resolution")}

    monkeypatch.setattr(support_agent, "support_investigation_agent", UnsupportedSupportAgent())
    investigation = support_agent.investigate_support_ticket(ticket)

    assert investigation.result.outcome == "engineer_escalation"
    assert investigation.result.supporting_facts == []
    assert investigation.tools_used == ["get_platform_status"]
