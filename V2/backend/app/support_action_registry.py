"""Supported actions and their policy, owned by Python rather than the model."""

ACTION_REGISTRY = {
    "retry_order_sync": {"risk_level": "low", "approval_requirement": "user_confirmation", "builder": "build_order_action_plan", "executor": "execute_order_recovery", "verifier": "wait_for_order_verification", "resource": "orders"},
    "resend_shipment": {"risk_level": "low", "approval_requirement": "user_confirmation", "builder": "build_shipment_action_plan", "executor": "execute_shipment_recovery", "verifier": "wait_for_shipment_verification", "resource": "shipments"},
    "refresh_inventory": {"risk_level": "low", "approval_requirement": "user_confirmation", "builder": "build_inventory_action_plan", "executor": "execute_background_action", "verifier": "wait_for_background_verification", "resource": "inventory"},
    "retry_failed_task": {"risk_level": "low", "approval_requirement": "user_confirmation", "builder": "build_order_action_plan", "executor": "execute_background_action", "verifier": "wait_for_background_verification", "resource": "tasks"},
    "request_reauthorization": {"risk_level": "low", "approval_requirement": "user_action", "builder": "build_reauthorization_action", "executor": None, "verifier": None, "resource": None},
}

# Existing persisted plans and old API clients can still be read.
ACTION_ALIASES = {"recover_order": "retry_order_sync", "recover_shipment": "resend_shipment"}


def action_policy(action: dict[str, object]) -> dict[str, object]:
    action_type = ACTION_ALIASES.get(str(action["action_type"]), str(action["action_type"]))
    if action_type not in ACTION_REGISTRY:
        raise ValueError(f"Unsupported action: {action_type}")
    policy = dict(ACTION_REGISTRY[action_type])
    # Changing a shop-wide setting is broader than retrying one business object.
    if action.get("enable_order_sync") or action.get("enable_shipment_sync"):
        policy.update(risk_level="medium", approval_requirement="admin")
    return policy
