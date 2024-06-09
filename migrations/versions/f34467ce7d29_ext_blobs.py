"""External reports

Revision ID: f34467ce7d29
Revises: 405672cf4046
Create Date: 2024-06-08 18:27:39.429461

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f34467ce7d29"
down_revision: Union[str, None] = "405672cf4046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the blob_name column to the demo_sessions table."""
    op.execute(
        sa.text(
            """
        ALTER TABLE demo_sessions
        ADD COLUMN blob_name TEXT;
        """
        )
    )


def downgrade() -> None:
    """Drop the blob_name column from the demo_sessions table."""
    op.execute(
        sa.text(
            """
        ALTER TABLE demo_sessions
        DROP COLUMN blob_name;
        """
        )
    )
