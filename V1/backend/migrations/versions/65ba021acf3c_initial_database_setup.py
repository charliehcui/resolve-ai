"""initial database setup

Revision ID: 65ba021acf3c
Revises:
Create Date: 2026-08-25 15:35:36.493411

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '65ba021acf3c'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
