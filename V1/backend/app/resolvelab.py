import httpx

from app.core.config import settings


def get_resolvelab_data(path: str, expect_list: bool = False) -> dict[str, object] | list[dict[str, object]]:
    try:
        response = httpx.get(f"{settings.resolvelab_base_url}{path}", timeout=5.0)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as error:
        return {"status": "error", "error": {"code": "http_error", "message": "ResolveLab request failed", "http_status": error.response.status_code}}
    except httpx.RequestError:
        return {"status": "error", "error": {"code": "connection_error", "message": "ResolveLab is unavailable"}}
    except ValueError:
        return {"status": "error", "error": {"code": "invalid_response", "message": "ResolveLab returned invalid JSON"}}

    if expect_list and isinstance(data, list) and all(isinstance(item, dict) for item in data):
        return data

    if not expect_list and isinstance(data, dict):
        return data

    return {"status": "error", "error": {"code": "invalid_response", "message": "ResolveLab returned an unexpected data shape"}}


def post_resolvelab_data(path: str, data: dict[str, object]) -> dict[str, object]:
    try:
        response = httpx.post(f"{settings.resolvelab_base_url}{path}", json=data, timeout=5.0)
        response.raise_for_status()
        response_data = response.json()
    except httpx.HTTPStatusError as error:
        return {"status": "error", "error": {"code": "http_error", "message": "ResolveLab request failed", "http_status": error.response.status_code}}
    except httpx.RequestError:
        return {"status": "error", "error": {"code": "connection_error", "message": "ResolveLab is unavailable"}}
    except ValueError:
        return {"status": "error", "error": {"code": "invalid_response", "message": "ResolveLab returned invalid JSON"}}

    if isinstance(response_data, dict):
        return response_data

    return {"status": "error", "error": {"code": "invalid_response", "message": "ResolveLab returned an unexpected data shape"}}
