from uuid import uuid4

from app.actions import propose_order_recovery
from app.auth import authenticate
from app.db import get_connection
from app.verify import verify_order_recovery
from tests.test_actions import action_runtime as setup_action_runtime
from tests.test_actions import create_missing_order_case


def test_failed_readback_never_marks_action_resolved(seeded_database: dict[str, str], monkeypatch) -> None:
    action_runtime = setup_action_runtime.__wrapped__(seeded_database, monkeypatch)
    auth = authenticate(action_runtime["token_a"])
    case_id, _, _ = create_missing_order_case(auth, "O-VERIFY-FAIL")
    proposed = propose_order_recovery(auth, case_id)
    with get_connection() as connection:
        connection.execute("UPDATE support.action_proposals SET status = 'awaiting_verification' WHERE action_id = %s", (proposed["action_id"],))
        connection.execute("INSERT INTO support.action_decisions (decision_id, action_id, decision, decided_by) VALUES (%s, %s, 'approved', %s)", (str(uuid4()), proposed["action_id"], auth.user_id))
    result = verify_order_recovery(auth, proposed["action_id"], final=True)
    assert result["status"] == "verification_failed"
    assert result["verification"]["status"] == "verification_failed"
