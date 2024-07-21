"""remove-beta-req

Revision ID: 9f376f19995e
Revises: 82f4e558463f
Create Date: 2024-07-21 08:22:37.388285

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f376f19995e'
down_revision: Union[str, None] = '82f4e558463f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DROP TABLE beta_tester_steam_ids;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE beta_tester_steam_ids (
            steam_id varchar PRIMARY KEY
        );
        """
    )
