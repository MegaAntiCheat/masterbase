"""reports

Revision ID: 405672cf4046
Revises: 58fb39990d30
Create Date: 2024-05-20 19:51:50.881864

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "405672cf4046"
down_revision: Union[str, None] = "58fb39990d30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the reports table."""
    op.execute(
        """
        CREATE TABLE reports (
            session_id varchar REFERENCES demo_sessions,
            target_steam_id varchar,
            created_at timestamptz,
            PRIMARY KEY (session_id, target_steam_id)
        );
        """
    )


def downgrade() -> None:
    """Remove the reports table."""
    op.execute(
        """
        DROP TABLE reports;
        """
    )
