"""report reasons

Revision ID: f36ba7b55e49
Revises: 405672cf4046
Create Date: 2024-06-07 16:49:45.487922

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f36ba7b55e49"
down_revision: Union[str, None] = "405672cf4046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE report_reason AS ENUM ('bot', 'cheater');

        ALTER TABLE reports
        ADD COLUMN reason report_reason;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE reports
        DROP COLUMN reason;

        DROP TYPE report_reason;
        """
    )
