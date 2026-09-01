from app.agent import investigation_tools


def test_agent_only_has_read_tools() -> None:
    tool_names = []

    for current_tool in investigation_tools:
        tool_names.append(current_tool.name)

    assert tool_names == [
        "get_customer_account",
        "get_event_notification_deliveries",
        "get_platform_status",
    ]
