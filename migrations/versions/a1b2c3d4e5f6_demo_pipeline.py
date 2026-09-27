"""Add demo_pipeline and demo_claims tables

Revision ID: a1b2c3d4e5f6
Revises: eba5782c5979
Create Date: 2026-08-26

Adds a pipeline table with one row per session and boolean columns for
each pipeline stage (compressed, analyzed). Task order is defined in code,
not in the database. Also adds demo_claims for external analysis client
claims, and removes the now-unused ingested column from demo_sessions.
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
    """Create demo_pipeline and demo_claims, remove ingested from demo_sessions."""
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

        -- Backfill from demo_sessions. No prior analysis state exists on main,
        -- so every session starts unanalyzed and gets picked up by the pipeline.
        INSERT INTO demo_pipeline (session_id, compressed, analyzed, created_at, updated_at)
        SELECT session_id, false, false, NOW(), NOW()
        FROM demo_sessions;

        -- Remove the legacy ingested column from demo_sessions
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
    """Drop demo_pipeline and demo_claims, restore ingested column on demo_sessions."""
    op.execute(
        """
        DROP TABLE IF EXISTS demo_claims;
        DROP TABLE IF EXISTS demo_pipeline;

        -- Restore the legacy ingested column. Pipeline state is lost;
        -- sessions will appear uningested until re-analyzed.
        ALTER TABLE demo_sessions ADD COLUMN ingested boolean DEFAULT false;
        """
    )
