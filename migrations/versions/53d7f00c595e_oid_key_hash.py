"""oid-key-hash

Revision ID: 53d7f00c595e
Revises: 9f376f19995e
Create Date: 2024-07-21 20:48:37.755305

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '53d7f00c595e'
down_revision: Union[str, None] = '9f376f19995e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE api_keys ADD COLUMN oid_hash varchar;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE api_keys DROP COLUMN oid_hash;
        """
    )
