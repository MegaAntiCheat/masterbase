"""Analysis ingestion logic."""

import io
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from minio import Minio
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from masterbase.models import Analysis
from masterbase.lib import json_blob_name


def get_uningested_demos(engine: Engine, limit: int) -> list[str]:
    """Get a list of demos that need analysis.
    
    Returns sessions that are closed, not pruned, and not yet analyzed
    in the pipeline.
    """
    sql = """
        SELECT
            ds.session_id
        FROM
            demo_sessions ds
        LEFT JOIN demo_pipeline dp ON ds.session_id = dp.session_id
        WHERE
            ds.active = false
            AND ds.open = false
            AND ds.pruned = false
            AND ds.demo_size > 0
            AND (dp.analyzed IS NULL OR dp.analyzed = false)
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


def ingest_demos(minio_client: Minio, engine: Engine, session_ids: list[str]) -> dict[str, str | None]:
    """Ingest a list of demos from an analysis client."""

    # preprocessing of data
    results = dict[str, Analysis]()
    errors = dict[str, str | None]()
    for session_id in session_ids:
        result = ingest_preprocess_analysis(minio_client, session_id)
        if result is str:
            errors[session_id] = result
        else:
            results[session_id] = result
            errors[session_id] = None

    # SQL query to wipe existing analysis data
    wipe_analysis_sql = "DELETE FROM analysis WHERE session_id = ANY(:session_ids);"

    # SQL query to insert the analysis data
    insert_sql = """\
        INSERT INTO analysis (
            session_id, target_steam_id, algorithm_type, detection_count, created_at
        ) VALUES (
            :session_id, :target_steam_id, :algorithm, :count, :created_at
        );
    """

    created_at = datetime.now().astimezone(timezone.utc).isoformat()

    ingestable_results = dict[str, dict[str, int]]()

    # Check each demo is actually ingestable
    with engine.begin() as conn:
        for session_id in list(results.keys()):
            error = _check_session_ready(conn, session_id)
            if error:
                errors[session_id] = error
            else:
                ingestable_results[session_id] = results[session_id]

    results = ingestable_results

    from masterbase.tasks import TASK_ANALYZE, mark_stage_done

    with engine.begin() as conn:
        result_list = list(results.keys())
        conn.execute(
            sa.text(wipe_analysis_sql),
            {"session_ids": result_list},
        )

        for session_id, algorithm_counts in results.items():
            for key, count in algorithm_counts.items():
                conn.execute(
                    sa.text(insert_sql),
                    {
                        "session_id": session_id,
                        "target_steam_id": key[0],
                        "algorithm": key[1],
                        "count": count,
                        "created_at": created_at,
                    },
                )

        # Mark all as analyzed in pipeline
        for session_id in result_list:
            mark_stage_done(engine, session_id, TASK_ANALYZE)

    return errors


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

    return None
