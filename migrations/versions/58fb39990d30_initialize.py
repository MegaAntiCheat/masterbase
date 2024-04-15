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
            steam_id varchar,
            api_key varchar,
            created_at timestamptz,
            updated_at timestamptz,
            PRIMARY KEY (steam_id)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE losers (
            steam_id varchar,
            created_at timestamptz,
            updated_at timestamptz,
            PRIMARY KEY (steam_id)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE demo_sessions (
            steam_id varchar REFERENCES api_keys,
            session_id varchar,
            demo_name varchar,
            active boolean,
            open boolean,
            start_time timestamptz,
            end_time timestamptz,
            fake_ip varchar,
            map varchar,
            steam_api_data jsonb,
            ingested boolean,
            demo_oid oid,
            demo_size integer,
            late_bytes bytea,
            created_at timestamptz,
            updated_at timestamptz,
            PRIMARY KEY (session_id)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE beta_tester_steam_ids (
            steam_id varchar PRIMARY KEY
        );
        """
    )


    op.execute(
        """
        CREATE TABLE analyst_steam_ids (
            steam_id varchar PRIMARY KEY
        );
        """
    )

def downgrade() -> None:
    op.execute(
        """
        DROP TABLE demo_sessions;
        DROP TABLE api_keys;
        DROP TABLE beta_tester_steam_ids;
        DROP TABLE analyst_steam_ids;
        """
    )
