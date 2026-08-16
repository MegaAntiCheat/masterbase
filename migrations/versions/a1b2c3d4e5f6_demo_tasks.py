"""Add demo_tasks table for background task queue

Revision ID: a1b2c3d4e5f6
Revises: eba5782c5979
Create Date: 2026-08-11

Creates a task queue table for managing background operations on demos
like compression, backups, etc.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'eba5782c5979'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create demo_tasks table for background task queue."""
    op.execute(
        """
        CREATE TABLE demo_tasks (
            id SERIAL PRIMARY KEY,
            session_id varchar,
            task_type varchar NOT NULL,
            status varchar NOT NULL DEFAULT 'pending',
            priority integer DEFAULT 0,
            worker_id varchar,
            attempted_count integer DEFAULT 0,
            max_attempts integer DEFAULT 3,
            error_message text,
            created_at timestamptz DEFAULT NOW(),
            updated_at timestamptz DEFAULT NOW(),
            completed_at timestamptz
        );

        CREATE INDEX idx_demo_tasks_status_type ON demo_tasks (status, task_type, priority DESC, created_at ASC);
        """
    )


def downgrade() -> None:
    """Remove demo_tasks table."""
    op.execute("DROP TABLE IF EXISTS demo_tasks;")
