"""add support investigation result

Revision ID: f2a7c91d4e6b
Revises: 8b3d1f5c9a20
Create Date: 2026-09-11 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a7c91d4e6b"
down_revision: Union[str, Sequence[str], None] = "8b3d1f5c9a20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("support_sessions", sa.Column("customer_result", sa.Text(), nullable=True))
    op.add_column("tickets", sa.Column("investigation_result", sa.JSON(), nullable=True))
    op.add_column("tickets", sa.Column("investigation_tools", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tickets", "investigation_tools")
    op.drop_column("tickets", "investigation_result")
    op.drop_column("support_sessions", "customer_result")
