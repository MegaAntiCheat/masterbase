"""Replace demo_tasks with demo_pipeline table

Revision ID: a1b2c3d4e5f6
Revises: eba5782c5979
Create Date: 2026-08-26

Replaces the complex task queue (demo_tasks) with a simpler pipeline table.
One row per session with boolean columns for each pipeline stage.
Task order is defined in code, not in the database.
Removes the ingested column from demo_sessions.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'eba5782c5979'
branch_labels: Union[str, Sequence[str, None]] = None
depends_on: Union[str, Sequence[str, None]] = None


def upgrade() -> None:
    """Create demo_pipeline and demo_claims tables, drop demo_tasks, remove ingested from demo_sessions."""
    op.execute(
        """
        CREATE TABLE demo_pipeline (
            session_id varchar PRIMARY KEY,
            compressed boolean NOT NULL DEFAULT false,
            analyzed boolean NOT NULL DEFAULT false,
            error_message text,
            created_at timestamptz DEFAULT NOW(),
            updated_at timestamptz DEFAULT NOW()
        );

        -- Migrate from demo_tasks and demo_sessions.ingested
        INSERT INTO demo_pipeline (session_id, compressed, analyzed, created_at, updated_at)
        SELECT DISTINCT ON (session_id)
            session_id,
            false,
            COALESCE(ds.ingested, false),
            NOW(),
            NOW()
        FROM demo_sessions ds
        ON CONFLICT (session_id) DO NOTHING;

        DROP TABLE IF EXISTS demo_tasks;

        -- Remove ingested column from demo_sessions
        ALTER TABLE demo_sessions DROP COLUMN IF EXISTS ingested;

        -- Create demo_claims table for external analysis client claims
        CREATE TABLE demo_claims (
            session_id varchar PRIMARY KEY REFERENCES demo_pipeline(session_id),
            client_ip inet NOT NULL,
            state varchar NOT NULL DEFAULT 'active',
            claimed_at timestamptz NOT NULL DEFAULT NOW(),
            released_at timestamptz
        );
        """
    )


def downgrade() -> None:
    """Restore demo_tasks table, restore ingested column, drop demo_pipeline and demo_claims."""
    op.execute(
        """
        CREATE TABLE demo_tasks (
            id SERIAL PRIMARY KEY,
            session_id varchar,
            task_type varchar NOT NULL DEFAULT 'compress',
            status varchar NOT NULL DEFAULT 'completed',
            priority integer DEFAULT 0,
            worker_id varchar,
            attempted_count integer DEFAULT 0,
            max_attempts integer DEFAULT 3,
            error_message text,
            created_at timestamptz DEFAULT NOW(),
            updated_at timestamptz DEFAULT NOW(),
            completed_at timestamptz DEFAULT NOW(),
            UNIQUE (session_id, task_type)
        );

        -- Restore analyze tasks from pipeline
        INSERT INTO demo_tasks (session_id, task_type, status, created_at, updated_at, completed_at)
        SELECT session_id, 'analyze', 'completed', created_at, updated_at, updated_at
        FROM demo_pipeline
        WHERE analyzed = true;

        -- Restore ingested column from pipeline
        ALTER TABLE demo_sessions ADD COLUMN ingested boolean DEFAULT false;
        UPDATE demo_sessions SET ingested = dp.analyzed
        FROM demo_pipeline dp
        WHERE demo_sessions.session_id = dp.session_id;

        DROP TABLE IF EXISTS demo_claims;
        DROP TABLE IF EXISTS demo_pipeline;
        """
    )
