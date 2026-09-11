import pytest

from app import classification
from app.classification import ClassificationRequest, ClassificationResult, TicketCategory, TicketSeverity


def test_classification_returns_internal_fields_in_english(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_result = ClassificationResult(
        category=TicketCategory.EVENT_NOTIFICATION_FAILURE,
        severity=TicketSeverity.MEDIUM,
        affected_feature="event notifications",
        summary="Order completion notifications return HTTP 401.",
        missing_information=[],
        urgency_reason="Notifications continue to fail for one customer.",
    )

    class FakeClassificationModel:
        def invoke(self, messages: list[object]) -> ClassificationResult:
            assert "Use English for every natural-language field" in messages[0].content
            return expected_result

    monkeypatch.setattr(classification, "classification_model", FakeClassificationModel())

    request = ClassificationRequest(title="订单通知失败", description="订单通知两次返回 HTTP 401。", customer_id="customer_001")
    result = classification.classify_ticket(request)

    assert result == expected_result
