"""broadcasts

Revision ID: f51cab87d3fd
Revises: b941ebee3091
Create Date: 2025-02-10 17:03:28.325372

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f51cab87d3fd'
down_revision: Union[str, None] = 'b941ebee3091'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add Broadcasts table"""
    op.execute(
        """
        CREATE TABLE broadcasts (
            message varchar,
            importance varchar,
            created_at timestamptz,
            PRIMARY KEY (message)
        );
        """
    )


def downgrade() -> None:
    """Delete broadcasts table"""
    op.execute(
        """
        DROP TABLE broadcasts;
        """
    )