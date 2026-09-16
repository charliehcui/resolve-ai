import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import actions, main, support_sessions, tickets
from app.db.database import Base
from app.db.models import SupportSession, Ticket
from app.handoff import SupportHandoff

client = TestClient(main.app)


@pytest.fixture
def test_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    monkeypatch.setattr(actions, "SessionLocal", test_session)
    monkeypatch.setattr(support_sessions, "SessionLocal", test_session)
    monkeypatch.setattr(tickets, "SessionLocal", test_session)

    yield test_session

    Base.metadata.drop_all(engine)
    engine.dispose()


def build_handoff(customer_id: str = "customer_001") -> SupportHandoff:
    return SupportHandoff(
        support_session_id="session_001",
        customer_id=customer_id,
        issue_summary="Order notifications are not delivered.",
        affected_feature="order notifications",
        customer_impact="The customer cannot receive order status updates.",
        approximate_start_time="This morning",
        environment_snapshot={"product_version": "2026.8"},
        collected_facts=[],
        attempted_steps=["重新保存通知地址。"],
        citation_ids=["docs/customer/order-notifications.md:0"],
        remaining_questions=[],
        handoff_reason="The customer confirmed that the proposed steps did not resolve the problem.",
    )


def test_create_ticket_from_handoff_and_read_it(test_database) -> None:
    support_sessions.create_support_session_record("session_001", "customer_001", "thread_001")
    handoff = build_handoff()

    ticket_id = tickets.create_ticket_from_handoff(handoff)
    read_response = client.get(f"/api/v1/tickets/{ticket_id}")

    assert read_response.status_code == 200
    assert read_response.json()["support_session_id"] == "session_001"
    assert read_response.json()["handoff"] == handoff.model_dump(mode="json")
    assert read_response.json()["status"] == "OPEN"

    with test_database() as database:
        ticket = database.get(Ticket, ticket_id)
        support_session = database.get(SupportSession, "session_001")

        assert ticket is not None
        assert ticket.legacy_customer_id is None
        assert ticket.legacy_title is None
        assert ticket.legacy_description is None
        assert ticket.legacy_classification is None
        assert support_session is not None
        assert support_session.status == "needs_assistance"
        assert hasattr(support_session, "ticket_id") is False
        assert hasattr(support_session, "handoff") is False


def test_same_support_session_only_creates_one_ticket(test_database) -> None:
    support_sessions.create_support_session_record("session_001", "customer_001", "thread_001")
    handoff = build_handoff()

    first_ticket_id = tickets.create_ticket_from_handoff(handoff)
    second_ticket_id = tickets.create_ticket_from_handoff(handoff)

    with test_database() as database:
        ticket_count = database.scalar(select(func.count()).select_from(Ticket))

    assert second_ticket_id == first_ticket_id
    assert ticket_count == 1


def test_handoff_cannot_change_the_session_customer(test_database) -> None:
    support_sessions.create_support_session_record("session_001", "customer_001", "thread_001")

    with pytest.raises(ValueError, match="customer does not match"):
        tickets.create_ticket_from_handoff(build_handoff(customer_id="customer_999"))

    with test_database() as database:
        ticket_count = database.scalar(select(func.count()).select_from(Ticket))

    assert ticket_count == 0
