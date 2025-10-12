"""empty message

Revision ID: af01d23e698e
Revises: eba5782c5979
Create Date: 2025-10-12 11:27:58.692283

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'af01d23e698e'
down_revision: Union[str, None] = 'eba5782c5979'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

from alembic import op


def upgrade():
    op.execute("""
        DROP TABLE reviews;
    """)

    # actions:
    # - create_case: creates the case.
    # - publish_case: exposes the case to public review
    # - withdraw_case: hides the case from public review

    # - set_judgement: sets the overall judgement for the case
    #   - verdict: 'none' | 'benign' | 'inconclusive' | 'confirmed' | 'error'
    #   - reasoning: text (max 2048 chars)
    # - set_review: sets an individual's review for the case.
    #   - verdict: 'none' | 'benign' | 'inconclusive' | 'confirmed' | 'error'
    #   - reasoning: text (max 2048 chars)
    #   - reviewer_steam_id: the reviewer
    #   - session_id: the evidence they're commenting on (NULL for general review)

    op.execute("""
        CREATE TABLE cases
        (
            target_steam_id varchar PRIMARY KEY,
            action varchar,
            parameters jsonb,
            created_at timestamptz DEFAULT NOW()
        );
    """)


def downgrade():
    op.execute(
        """
        CREATE TABLE reviews (
            session_id varchar REFERENCES demo_sessions,
            target_steam_id varchar,
            reviewer_steam_id varchar,
            verdict verdict,
            created_at timestamptz,
            PRIMARY KEY (session_id, target_steam_id, reviewer_steam_id)
        );
        """
    )
