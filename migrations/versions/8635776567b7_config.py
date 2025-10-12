"""empty message

Revision ID: 8635776567b7
Revises: af01d23e698e
Create Date: 2025-10-12 20:54:41.707642

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8635776567b7'
down_revision: Union[str, None] = 'af01d23e698e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    op.execute("""
        CREATE TABLE config (
            setting TEXT PRIMARY KEY,
            value TEXT
        );
    """)

    op.execute("""
        INSERT INTO config (setting, value)
        SELECT 'max_storage_gb', max_storage_gb::TEXT FROM prune_config;
    """)
    op.execute("""
        INSERT INTO config (setting, value)
        SELECT 'max_prune_ratio', max_prune_ratio::TEXT FROM prune_config;
    """)

    op.execute("DROP TABLE prune_config;")


def downgrade():
    op.execute("""
        CREATE TABLE prune_config (
            max_storage_gb INTEGER PRIMARY KEY DEFAULT 0,
            max_prune_ratio DOUBLE PRECISION DEFAULT 0.05
        );
    """)

    op.execute("""
        INSERT INTO prune_config (max_storage_gb, max_prune_ratio)
        SELECT
            CAST((SELECT value FROM config WHERE setting = 'max_storage_gb') AS INTEGER),
            CAST((SELECT value FROM config WHERE setting = 'max_prune_ratio') AS DOUBLE PRECISION);
    """)

    op.execute("DROP TABLE config;")