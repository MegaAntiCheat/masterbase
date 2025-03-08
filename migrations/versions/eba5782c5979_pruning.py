"""pruning

Revision ID: eba5782c5979
Revises: f51cab87d3fd
Create Date: 2025-03-08 13:46:16.132860

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eba5782c5979'
down_revision: Union[str, None] = 'f51cab87d3fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add pruned column to demo_sessions and default to false"""
    op.execute(
        """
        ALTER TABLE demo_sessions
        ADD COLUMN pruned boolean;

        UPDATE demo_sessions
        SET pruned = false;

        ALTER TABLE demo_sessions
        ALTER COLUMN pruned SET DEFAULT false;

        ALTER TABLE demo_sessions
        ALTER COLUMN pruned SET NOT NULL;
        """
    )

    op.execute(
        """
        CREATE TABLE prune_config (
            min_free_space_gb integer
        );

        INSERT INTO prune_config (min_free_space_gb)
            VALUES (50)
        """
    )

def downgrade() -> None:
    """Remove pruned column from demo_sessions"""
    op.execute(
        """
        ALTER TABLE demo_sessions
        DROP COLUMN pruned;

        DROP TABLE prune_config;
        """
    )
