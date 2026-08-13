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
    """Get a list of uningested demos."""
    sql = """
        SELECT
            session_id
        FROM
            demo_sessions
        WHERE
            active = false
            AND open = false
            AND ingested = false
            AND pruned = false
            AND demo_size > 0
        ORDER BY
            created_at ASC
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
    
    # SQL query to ensure the demo sessions are not already ingested
    is_ingested_sql = "SELECT session_id, ingested, active, open FROM demo_sessions WHERE session_id = ANY(:session_ids);"

    # SQL query to wipe existing analysis data
    # (we want to be able to reingest a demo if necessary by manually setting ingested = false)
    wipe_analysis_sql = "DELETE FROM analysis WHERE session_id = ANY(:session_ids);"

    # SQL query to insert the analysis data
    insert_sql = """\
        INSERT INTO analysis (
            session_id, target_steam_id, algorithm_type, detection_count, created_at
        ) VALUES (
            :session_id, :target_steam_id, :algorithm, :count, :created_at
        );
    """

    # SQL query to mark the demo as ingested
    mark_ingested_sql = "UPDATE demo_sessions SET ingested = true WHERE session_id = ANY(:session_ids);"
    created_at = datetime.now().astimezone(timezone.utc).isoformat()

    ingestable_results = dict[str, dict[str, int]]()

    # Check demo is actually ingestable
    with engine.connect() as conn:
        with conn.begin():
            result_list = list(results.keys())
            command = conn.execute(
                sa.text(is_ingested_sql),
                {"session_ids": result_list},
            )
            query_results = command.all()

            for result in query_results:
                session_id = result.session_id
                if result.ingested is True:
                    errors[session_id] = "demo already ingested"
                    continue
                if result.active is True:
                    errors[session_id] = "session is still active"
                    continue
                if result.open is True:
                    errors[session_id] = "session is still open"
                    continue
                ingestable_results[session_id] = results[session_id]
    
    results = ingestable_results

    with engine.connect() as conn:
        with conn.begin():
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

            conn.execute(
                sa.text(mark_ingested_sql),
                {"session_ids": result_list},
            )
            
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
    
    check_sql = "SELECT session_id, ingested, active, open FROM demo_sessions WHERE session_id = :session_id;"
    wipe_sql = "DELETE FROM analysis WHERE session_id = :session_id;"
    insert_sql = """\
        INSERT INTO analysis (
            session_id, target_steam_id, algorithm_type, detection_count, created_at
        ) VALUES (
            :session_id, :target_steam_id, :algorithm, :count, :created_at
        );
    """
    mark_sql = "UPDATE demo_sessions SET ingested = true WHERE session_id = :session_id;"
    
    created_at = datetime.now().astimezone(timezone.utc).isoformat()
    
    # Preprocess: count detections by (player, algorithm)
    algorithm_counts = AnalysisSummary()
    for detection in analysis.detections:
        key = (str(detection.player), detection.algorithm)
        if key not in algorithm_counts:
            algorithm_counts[key] = 0
        algorithm_counts[key] += 1
    
    with engine.begin() as conn:
        # Check session state
        row = conn.execute(sa.text(check_sql), {"session_id": session_id}).fetchone()
        if row is None:
            return "Unknown session_id"
        if row.ingested:
            return "demo already ingested"
        if row.active:
            return "session is still active"
        if row.open:
            return "session is still open"
        
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
        
        # Mark as ingested
        conn.execute(sa.text(mark_sql), {"session_id": session_id})
    
    return None