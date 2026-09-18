from datetime import UTC, datetime


def parse_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def assess_stock_facts(merchant: dict[str, object], warehouse: dict[str, object] | None, platform: dict[str, object] | None, now: datetime | None = None, propagation_window_seconds: int = 30) -> dict[str, object]:
    if merchant.get("empty") is True:
        return {"assessment": "insufficient_information", "reason": "STOCK_MAPPING_MISSING"}
    rule = merchant.get("rule")
    if not isinstance(rule, dict) or warehouse is None:
        return {"assessment": "insufficient_information", "reason": "SOURCE_FACTS_MISSING"}
    required = ("physical_quantity", "reserved_quantity", "version", "updated_at")
    if any(warehouse.get(field) is None for field in required) or rule.get("safety_stock") is None:
        return {"assessment": "insufficient_information", "reason": "SOURCE_VERSION_OR_TIME_MISSING"}
    observed_at = parse_time(warehouse["updated_at"])
    if observed_at is None:
        return {"assessment": "insufficient_information", "reason": "SOURCE_TIME_INVALID"}
    current_time = now or datetime.now(UTC)
    age_seconds = max((current_time - observed_at).total_seconds(), 0)
    expected = max(int(warehouse["physical_quantity"]) - int(warehouse["reserved_quantity"]) - int(rule["safety_stock"]), 0)
    if platform is None or platform.get("source_version") is None:
        if age_seconds <= propagation_window_seconds:
            return {"assessment": "waiting", "reason": "WITHIN_PROPAGATION_WINDOW", "expected_quantity": expected, "source_version": warehouse["version"]}
        return {"assessment": "difference", "reason": "PLATFORM_STOCK_MISSING", "expected_quantity": expected, "source_version": warehouse["version"]}
    if int(platform["source_version"]) != int(warehouse["version"]):
        if age_seconds <= propagation_window_seconds:
            return {"assessment": "waiting", "reason": "VERSION_PROPAGATION_PENDING", "expected_quantity": expected, "source_version": warehouse["version"]}
        return {"assessment": "difference", "reason": "VERSION_NOT_PUBLISHED", "expected_quantity": expected, "source_version": warehouse["version"], "platform_version": platform["source_version"]}
    if platform.get("quantity") != expected:
        return {"assessment": "difference", "reason": "QUANTITY_MISMATCH", "expected_quantity": expected, "platform_quantity": platform.get("quantity"), "source_version": warehouse["version"]}
    return {"assessment": "consistent", "reason": "MATCHED_SOURCE_VERSION", "expected_quantity": expected, "source_version": warehouse["version"]}
