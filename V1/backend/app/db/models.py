from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SupportSession(Base):
    __tablename__ = "support_sessions"
    __table_args__ = (UniqueConstraint("thread_id", name="uq_support_sessions_thread_id"),)

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(100))
    thread_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    final_problem_details: Mapped[dict[str, object] | None] = mapped_column(JSON)
    customer_result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (UniqueConstraint("support_session_id", name="uq_tickets_support_session_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    support_session_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("support_sessions.session_id", name="fk_tickets_support_session_id"))
    legacy_customer_id: Mapped[str | None] = mapped_column("customer_id", String(100))
    legacy_title: Mapped[str | None] = mapped_column("title", String(200))
    legacy_description: Mapped[str | None] = mapped_column("description", Text)
    legacy_classification: Mapped[dict[str, object] | None] = mapped_column("classification", JSON)
    handoff: Mapped[dict[str, object] | None] = mapped_column(JSON)
    investigation_result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    investigation_tools: Mapped[list[str] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ActionProposalRecord(Base):
    __tablename__ = "action_proposals"
    __table_args__ = (UniqueConstraint("ticket_id", name="uq_action_proposals_ticket_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", name="fk_action_proposals_ticket_id"))
    proposal: Mapped[dict[str, object]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))
    policy_reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ApprovalRecord(Base):
    __tablename__ = "approvals"
    __table_args__ = (UniqueConstraint("proposal_id", name="uq_approvals_proposal_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("action_proposals.id", name="fk_approvals_proposal_id"))
    decision: Mapped[str] = mapped_column(String(20))
    reviewer_role: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ActionExecutionRecord(Base):
    __tablename__ = "action_executions"
    __table_args__ = (UniqueConstraint("proposal_id", name="uq_action_executions_proposal_id"), UniqueConstraint("idempotency_key", name="uq_action_executions_idempotency_key"))

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("action_proposals.id", name="fk_action_executions_proposal_id"))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))
    request: Mapped[dict[str, object]] = mapped_column(JSON)
    before_state: Mapped[dict[str, object] | None] = mapped_column(JSON)
    after_state: Mapped[dict[str, object] | None] = mapped_column(JSON)
    external_reference: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
