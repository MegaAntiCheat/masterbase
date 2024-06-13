"""ext-blobs

Revision ID: 82f4e558463f
Revises: f36ba7b55e49
Create Date: 2024-06-12 20:10:56.301439

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '82f4e558463f'
down_revision: Union[str, None] = 'f36ba7b55e49'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the blob_name column to the demo_sessions table."""
    op.execute(
        sa.text(
            """
        ALTER TABLE demo_sessions
        ADD COLUMN blob_name TEXT,
        DROP COLUMN demo_oid;
        """
        )
    )


def downgrade() -> None:
    """Drop the blob_name column from the demo_sessions table."""
    op.execute(
        sa.text(
            """
        ALTER TABLE demo_sessions
        DROP COLUMN blob_name,
        ADD COLUMN demo_oid oid;
        """
        )
    )