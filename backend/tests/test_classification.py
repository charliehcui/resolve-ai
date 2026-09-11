import pytest

from app import classification
from app.classification import ClassificationRequest, ClassificationResult, TicketCategory, TicketSeverity


def test_classification_returns_the_model_result_in_chinese(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_result = ClassificationResult(
        category=TicketCategory.EVENT_NOTIFICATION_FAILURE,
        severity=TicketSeverity.MEDIUM,
        affected_feature="事件通知",
        summary="订单完成通知返回 HTTP 401。",
        missing_information=[],
        urgency_reason="单个客户的通知持续失败。",
    )

    class FakeClassificationModel:
        def invoke(self, messages: list[object]) -> ClassificationResult:
            assert "简体中文" in messages[0].content
            return expected_result

    monkeypatch.setattr(classification, "classification_model", FakeClassificationModel())

    request = ClassificationRequest(title="订单通知失败", description="订单通知两次返回 HTTP 401。", customer_id="customer_001")
    result = classification.classify_ticket(request)

    assert result == expected_result
