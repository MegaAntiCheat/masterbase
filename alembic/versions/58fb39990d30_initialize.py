"""initialize

Revision ID: 58fb39990d30
Revises: 
Create Date: 2023-12-30 16:24:24.579092

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58fb39990d30'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE api_keys (
            steam_id varchar PRIMARY KEY,
            api_key varchar
        );
        """
    )

    op.execute(
        """
        CREATE TABLE sessions (
            session_id varchar PRIMARY KEY,
            api_key varchar,
            active boolean
        );
        """
    )

def downgrade() -> None:
    op.execute(
        """
        DROP TABLE api_keys;
        DROP TABLE sessions;
        """
    )
