import pytest

from app import customer_tools, resolvelab


class FakeResponse:
    def __init__(self, response_data: dict[str, object]) -> None:
        self.response_data = response_data
        self.error_checked = False

    def raise_for_status(self) -> None:
        self.error_checked = True

    def json(self) -> dict[str, object]:
        return self.response_data


def test_get_current_product_context_uses_the_bound_customer_id(monkeypatch: pytest.MonkeyPatch) -> None:
    response_data = {"account_status": "active", "product_version": "2026.8", "affected_feature": "order notifications", "feature_enabled": True}
    fake_response = FakeResponse(response_data)

    def fake_get(url: str, timeout: float) -> FakeResponse:
        assert url == f"{resolvelab.settings.resolvelab_base_url}/customers/customer_001/product-context"
        assert timeout == 5.0
        return fake_response

    monkeypatch.setattr(resolvelab.httpx, "get", fake_get)

    result = customer_tools.get_current_product_context("customer_001")

    assert result == response_data
    assert fake_response.error_checked is True


def test_get_recent_customer_activity_uses_the_bound_customer_id(monkeypatch: pytest.MonkeyPatch) -> None:
    response_data = {"affected_feature": "order notifications", "activity": "Sending the latest order notification", "result": "failed", "occurred_at": "2026-08-25T09:20:00Z"}
    fake_response = FakeResponse(response_data)

    def fake_get(url: str, timeout: float) -> FakeResponse:
        assert url == f"{resolvelab.settings.resolvelab_base_url}/customers/customer_001/recent-activity"
        assert timeout == 5.0
        return fake_response

    monkeypatch.setattr(resolvelab.httpx, "get", fake_get)

    result = customer_tools.get_recent_customer_activity("customer_001")

    assert result == response_data
    assert fake_response.error_checked is True
