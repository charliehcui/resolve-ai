"""add support sessions and handoff

Revision ID: 8b3d1f5c9a20
Revises: 33454278effa
Create Date: 2026-09-11 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8b3d1f5c9a20"
down_revision: Union[str, Sequence[str], None] = "33454278effa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "support_sessions",
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("customer_id", sa.String(length=100), nullable=False),
        sa.Column("thread_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("final_problem_details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
        sa.UniqueConstraint("thread_id", name="uq_support_sessions_thread_id"),
    )
    op.add_column("tickets", sa.Column("support_session_id", sa.String(length=36), nullable=True))
    op.add_column("tickets", sa.Column("handoff", sa.JSON(), nullable=True))
    op.alter_column("tickets", "title", existing_type=sa.String(length=200), nullable=True)
    op.alter_column("tickets", "description", existing_type=sa.Text(), nullable=True)
    op.alter_column("tickets", "classification", existing_type=sa.JSON(), nullable=True)
    op.create_foreign_key("fk_tickets_support_session_id", "tickets", "support_sessions", ["support_session_id"], ["session_id"])
    op.create_unique_constraint("uq_tickets_support_session_id", "tickets", ["support_session_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_tickets_support_session_id", "tickets", type_="unique")
    op.drop_constraint("fk_tickets_support_session_id", "tickets", type_="foreignkey")
    op.execute("UPDATE tickets SET title = COALESCE(title, handoff->>'issue_summary', 'Legacy support ticket')")
    op.execute("UPDATE tickets SET description = COALESCE(description, handoff::text, 'No description available')")
    op.execute("""UPDATE tickets SET classification = COALESCE(classification, json_build_object('category', 'account_or_entitlement_mismatch', 'severity', 'medium', 'affected_feature', COALESCE(handoff->>'affected_feature', 'unknown'), 'summary', COALESCE(handoff->>'issue_summary', 'Legacy support ticket'), 'missing_information', '[]'::json, 'urgency_reason', 'Classification was restored during migration downgrade'))""")
    op.alter_column("tickets", "classification", existing_type=sa.JSON(), nullable=False)
    op.alter_column("tickets", "description", existing_type=sa.Text(), nullable=False)
    op.alter_column("tickets", "title", existing_type=sa.String(length=200), nullable=False)
    op.drop_column("tickets", "handoff")
    op.drop_column("tickets", "support_session_id")
    op.drop_table("support_sessions")
