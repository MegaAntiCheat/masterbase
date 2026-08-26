"""Analysis ingestion logic."""

import io
import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from minio import Minio
from sqlalchemy import Engine

from masterbase.models import Analysis
from masterbase.lib import json_blob_name

logger = logging.getLogger(__name__)


def get_uningested_demos(engine: Engine, limit: int) -> list[str]:
    """Get a list of demos that need analysis.

    Returns sessions that are closed, not pruned, not yet analyzed,
    and not claimed by an external client.
    """
    sql = """
        SELECT
            ds.session_id
        FROM
            demo_sessions ds
        LEFT JOIN demo_pipeline dp ON ds.session_id = dp.session_id
        LEFT JOIN demo_claims dc ON ds.session_id = dc.session_id
            AND dc.state IN ('active', 'released')
        WHERE
            ds.active = false
            AND ds.open = false
            AND ds.pruned = false
            AND ds.demo_size > 0
            AND (dp.analyzed IS NULL OR dp.analyzed = false)
            AND dc.session_id IS NULL
        ORDER BY
            ds.created_at ASC
        LIMIT :limit;
    """
    params = {"limit": limit}

    with engine.connect() as conn:
        result = conn.execute(
            sa.text(sql),
            params,
        )

        data = result.all()
        uningested_demos = [row[0] for row in data]

        return uningested_demos


def claim_session(engine: Engine, session_id: str, client_ip: str) -> bool:
    """Claim a session for external analysis.

    Creates an active claim if the session is not already claimed/released
    or already analyzed. Uses FOR UPDATE to atomically check and claim.
    Returns True if claim created, False otherwise.
    """
    with engine.begin() as conn:
        # Check if already analyzed
        result = conn.execute(
            sa.text(
                "SELECT analyzed FROM demo_pipeline WHERE session_id = :sid FOR UPDATE;"
            ),
            {"sid": session_id},
        ).fetchone()
        if result is None or result.analyzed:
            return False

        # Check for existing claim (active or released)
        claim = conn.execute(
            sa.text(
                "SELECT state FROM demo_claims WHERE session_id = :sid;"
            ),
            {"sid": session_id},
        ).fetchone()
        if claim is not None:
            return False

        # Create claim
        conn.execute(
            sa.text(
                """
                INSERT INTO demo_claims (session_id, client_ip, state, claimed_at)
                VALUES (:sid, :ip, 'active', NOW());
                """
            ),
            {"sid": session_id, "ip": client_ip},
        )
    logger.info("Session %s claimed by %s", session_id, client_ip)
    return True


def release_expired_claims(engine: Engine, timeout_minutes: int) -> int:
    """Release claims that have exceeded the timeout.

    Sets state to 'released' and records released_at timestamp.
    Returns the number of claims released.
    """
    with engine.begin() as conn:
        result = conn.execute(
            sa.text(
                """
                UPDATE demo_claims
                SET state = 'released', released_at = NOW()
                WHERE state = 'active'
                    AND claimed_at < NOW() - make_interval(mins => :min)
                RETURNING session_id;
                """
            ),
            {"min": timeout_minutes},
        )
        count = len(result.fetchall())
    if count:
        logger.info("Released %d expired claims", count)
    return count


def remove_claim(engine: Engine, session_id: str) -> None:
    """Remove a claim row entirely (used after analysis is complete).

    This is called when the demo is analyzed (internally or externally)
    to clean up the claim record.
    """
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM demo_claims WHERE session_id = :sid;"
            ),
            {"sid": session_id},
        )


def _check_session_ready(conn, session_id: str) -> str | None:
    """Check if a session is ready for analysis ingestion.
    
    Uses the provided connection (should be within a transaction).
    Ensures pipeline row exists, then checks session state.
    Returns error message or None if ready.
    """
    from masterbase.tasks import TASK_ANALYZE
    
    # Ensure pipeline row exists
    conn.execute(
        sa.text(
            """
            INSERT INTO demo_pipeline (session_id)
            VALUES (:sid)
            ON CONFLICT (session_id) DO NOTHING;
            """
        ),
        {"sid": session_id},
    )
    
    # Check analyzed status
    result = conn.execute(
        sa.text(
            "SELECT analyzed FROM demo_pipeline WHERE session_id = :sid;"
        ),
        {"sid": session_id},
    ).fetchone()
    if result and result.analyzed:
        return "demo already analyzed"
    
    # Check session state
    row = conn.execute(
        sa.text(
            "SELECT active, open FROM demo_sessions WHERE session_id = :sid;"
        ),
        {"sid": session_id},
    ).fetchone()
    if row is None:
        return "Unknown session_id"
    if row.active:
        return "session is still active"
    if row.open:
        return "session is still open"
    return None


AnalysisSummary = dict[tuple[str, str], int]


def submit_analysis(
    minio_client: Minio, engine: Engine, session_id: str, analysis: Analysis
) -> str | None:
    """Submit analysis results via HTTPS and ingest into database.
    
    Writes raw JSON to MinIO for archival, then ingests into DB.
    Returns error message or None on success.
    """
    # Write raw JSON to MinIO for archival
    blob_name = json_blob_name(session_id)
    try:
        json_bytes = analysis.model_dump_json().encode()
        minio_client.put_object(
            "jsonblobs", blob_name, io.BytesIO(json_bytes), len(json_bytes)
        )
    except Exception as err:
        return f"Failed to store analysis JSON: {err}"

    wipe_sql = "DELETE FROM analysis WHERE session_id = :session_id;"
    insert_sql = """\
        INSERT INTO analysis (
            session_id, target_steam_id, algorithm_type, detection_count, created_at
        ) VALUES (
            :session_id, :target_steam_id, :algorithm, :count, :created_at
        );
    """

    created_at = datetime.now().astimezone(timezone.utc).isoformat()

    # Preprocess: count detections by (player, algorithm)
    algorithm_counts = AnalysisSummary()
    for detection in analysis.detections:
        key = (str(detection.player), detection.algorithm)
        if key not in algorithm_counts:
            algorithm_counts[key] = 0
        algorithm_counts[key] += 1

    with engine.begin() as conn:
        # Check session state via pipeline helper
        error = _check_session_ready(conn, session_id)
        if error:
            return error

        # Wipe existing analysis
        conn.execute(sa.text(wipe_sql), {"session_id": session_id})

        # Insert new analysis data
        for (target_steam_id, algorithm), count in algorithm_counts.items():
            conn.execute(
                sa.text(insert_sql),
                {
                    "session_id": session_id,
                    "target_steam_id": target_steam_id,
                    "algorithm": algorithm,
                    "count": count,
                    "created_at": created_at,
                },
            )

        # Mark as analyzed in pipeline
        from masterbase.tasks import TASK_ANALYZE, mark_stage_done
        mark_stage_done(engine, session_id, TASK_ANALYZE)

        # Clean up claim if exists
        remove_claim(engine, session_id)

    return None
