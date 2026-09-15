import httpx
import pytest

from app import resolvelab
from app.customer_tools import get_current_product_context
from app.support_tools import get_background_operation


@pytest.mark.parametrize("failure", ["http", "connection", "json", "shape", "unexpected_list"])
def test_http_reads_return_structured_failures(monkeypatch, failure):
    def fake_get(url: str, timeout: float):
        request = httpx.Request("GET", url)

        if failure == "connection":
            raise httpx.ConnectError("Private transport details", request=request)
        if failure == "http":
            return httpx.Response(503, request=request, json={"detail": "Private response details"})
        if failure == "json":
            return httpx.Response(200, request=request, text="invalid json")
        if failure == "unexpected_list":
            return httpx.Response(200, request=request, json=[{"status": "active"}])
        return httpx.Response(200, request=request, json="unexpected scalar")

    monkeypatch.setattr(resolvelab.httpx, "get", fake_get)
    result = resolvelab.get_resolvelab_data("/platform-status")

    assert result["status"] == "error"
    assert result["error"]["code"] in ("http_error", "connection_error", "invalid_response")
    assert "Private" not in str(result)


def test_delivery_query_rejects_unexpected_dictionary(monkeypatch):
    monkeypatch.setattr(resolvelab.httpx, "get", lambda url, timeout: httpx.Response(200, request=httpx.Request("GET", url), json={"delivery_status": "failed"}))

    result = resolvelab.get_resolvelab_data("/customers/customer_001/event-notification-deliveries", expect_list=True)

    assert result["status"] == "error"
    assert result["error"]["code"] == "invalid_response"


def test_customer_ids_are_encoded_as_one_path_segment(monkeypatch):
    urls = []

    def fake_get(url: str, timeout: float):
        urls.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url), json={"status": "active"})

    monkeypatch.setattr(resolvelab.httpx, "get", fake_get)
    get_current_product_context("customer_001/../../platform-status")
    get_background_operation.invoke({"customer_id": "customer_001/../../platform-status"})

    assert all("customer_001%2F..%2F..%2Fplatform-status" in url for url in urls)
