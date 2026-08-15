"""Background cleanup tasks that run periodically and non-blocking."""

import logging
import os
import signal
import sys
import threading
from contextlib import contextmanager
from typing import Any

import sqlalchemy as sa
from minio import Minio, S3Error
from minio.datatypes import Object as BlobStat
from sqlalchemy import Engine

from masterbase.lib import demo_blob_name, json_blob_name, raw_blob_name

logger = logging.getLogger(__name__)

# Default timeout for individual MinIO operations (seconds)
MINIO_OPERATION_TIMEOUT: int = int(os.getenv("MINIO_TIMEOUT", "5"))


class TimeoutError(Exception):
    """Raised when an operation exceeds its timeout."""
    pass


@contextmanager
def timeout(seconds: int):
    """Context manager that raises TimeoutError if the block exceeds `seconds`."""
    if seconds <= 0:
        yield
        return

    def _raise_timeout(_signum, _frame):
        raise TimeoutError(f"Operation exceeded {seconds}s timeout")

    # Only use signal on main thread; fallback for threads
    try:
        old_handler = signal.signal(signal.SIGALRM, _raise_timeout)
        signal.alarm(seconds)
        try:
            yield
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    except (ValueError, TypeError):
        # signal not available in this thread (common in async/background threads)
        # Use a timer-based approach instead
        timer = threading.Timer(seconds, lambda: None)
        timed_out = threading.Event()

        def _check():
            timed_out.set()
            raise TimeoutError(f"Operation exceeded {seconds}s timeout")

        timer = threading.Timer(seconds, _check)
        timer.daemon = True
        timer.start()
        try:
            yield
        finally:
            timer.cancel()


def minio_operation_with_timeout(minio_client: Minio, operation: str, timeout: int = MINIO_OPERATION_TIMEOUT, **kwargs: Any) -> Any:
    """Execute a MinIO operation with a timeout wrapper.
    
    Args:
        minio_client: The MinIO client instance.
        operation: Name of the method to call (e.g. 'stat_object', 'list_objects', 'remove_object').
        timeout: Timeout in seconds.
        **kwargs: Arguments to pass to the MinIO method.
        
    Returns:
        The result of the MinIO operation.
        
    Raises:
        TimeoutError: If the operation exceeds the timeout.
    """
    method = getattr(minio_client, operation, None)
    if method is None:
        raise ValueError(f"Unknown MinIO operation: {operation}")
    
    result_holder: list = [None]
    error_holder: list = [None]
    
    def _run():
        try:
            result_holder[0] = method(**kwargs)
        except Exception as e:
            error_holder[0] = e
    
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    
    if thread.is_alive():
        logger.warning(f"MinIO operation '{operation}' timed out after {timeout}s with args {kwargs}")
        raise TimeoutError(f"MinIO {operation} timed out after {timeout}s")
    
    if error_holder[0] is not None:
        raise error_holder[0]
    
    return result_holder[0]


def list_objects_with_timeout(minio_client: Minio, bucket_name: str, timeout: int = MINIO_OPERATION_TIMEOUT):
    """List objects in a bucket with timeout protection.
    
    Returns a dictionary mapping object_name -> BlobStat.
    """
    try:
        result = minio_operation_with_timeout(
            minio_client, "list_objects", 
            timeout=timeout,
            bucket_name=bucket_name,
            recursive=True
        )
        return {blob.object_name: blob for blob in result}
    except TimeoutError:
        logger.warning(f"Timed out listing objects in bucket '{bucket_name}'. Skipping this bucket.")
        return {}
    except S3Error as e:
        logger.error(f"S3Error listing objects in bucket '{bucket_name}': {e}")
        return {}


def stat_object_with_timeout(minio_client: Minio, bucket_name: str, object_name: str, timeout: int = MINIO_OPERATION_TIMEOUT):
    """Get object stats with timeout protection."""
    try:
        return minio_operation_with_timeout(
            minio_client, "stat_object",
            timeout=timeout,
            bucket_name=bucket_name,
            object_name=object_name
        )
    except TimeoutError:
        logger.warning(f"Timed out stat-ing object '{object_name}' in '{bucket_name}'.")
        return None
    except S3Error as e:
        if e.code == "NoSuchKey":
            return None
        logger.error(f"S3Error stat-ing object '{object_name}': {e}")
        return None


def remove_object_with_timeout(minio_client: Minio, bucket_name: str, object_name: str, timeout: int = MINIO_OPERATION_TIMEOUT):
    """Remove an object with timeout protection."""
    try:
        minio_operation_with_timeout(
            minio_client, "remove_object",
            timeout=timeout,
            bucket_name=bucket_name,
            object_name=object_name
        )
        return True
    except TimeoutError:
        logger.warning(f"Timed out removing object '{object_name}' from '{bucket_name}'. Will retry next cycle.")
        return False
    except S3Error as e:
        logger.error(f"S3Error removing object '{object_name}': {e}")
        return False


def audit_storage_use(engine: Engine, minio_client: Minio) -> int:
    """Audit and fix demo_size for sessions with incorrect sizes.
    
    Returns the number of sessions updated.
    """
    logger.info("Auditing demo sizes in database...")
    updated = 0
    
    with engine.begin() as conn:
        results = conn.execute(
            sa.text(
                """
                SELECT session_id FROM demo_sessions
                WHERE (demo_size = 0 OR demo_size IS NULL) AND pruned = false
                LIMIT 100;
                """
            )
        )
        
        for row in results:
            session_id = row[0]  # Use index access for consistency
            # Try compressed first, then raw
            info = stat_object_with_timeout(minio_client, "demoblobs", demo_blob_name(session_id))
            if info is None:
                info = stat_object_with_timeout(minio_client, "rawblobs", raw_blob_name(session_id))
            if info is None:
                logger.info(f"Blob does not exist for session {session_id}")
                continue

            try:
                filesize = info.size
                conn.execute(
                    sa.text("""
                        UPDATE demo_sessions
                        SET demo_size = :filesize
                        WHERE session_id = :session_id
                    """),
                    {"filesize": filesize, "session_id": session_id}
                )
                logger.info(f"Updated session {session_id} with demo_size={filesize}")
                updated += 1
            except Exception as e:
                logger.error(f"Failed to set demo_size for {session_id}: {e}")
    
    if updated > 0:
        logger.info(f"Audited {updated} demo sizes.")
    else:
        logger.info("No demo sizes needed auditing.")
    
    return updated


def cleanup_hung_sessions(engine: Engine) -> int:
    """Remove any sessions that were left open/active after shutdown.
    
    Returns the number of sessions cleaned up.
    """
    logger.info("Checking for hanging sessions...")
    with engine.connect() as conn:
        result = conn.execute(
            sa.text(
                """
                DELETE FROM reports WHERE session_id IN (
                    SELECT session_id FROM demo_sessions
                    WHERE active = true
                    OR open = true
                    OR demo_size IS NULL
                );

                DELETE FROM demo_sessions
                WHERE active = true
                OR open = true
                OR demo_size IS NULL;
                """
            )
        )
        deleted_rows = result.rowcount
        conn.commit()
        logger.info("Deleted %d hanging sessions.", deleted_rows)
        return deleted_rows


def prune_if_necessary(engine: Engine, minio_client: Minio) -> int:
    """Mark sessions as pruned if storage exceeds the configured limit.
    
    Returns the number of demos marked for pruning.
    """
    logger.info("Checking if we need to prune demos...")
    
    # Get current storage usage from DB (much faster than querying MinIO)
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT SUM(demo_size) FROM demo_sessions WHERE pruned = false")
        )
        current_size = result.scalar() or 0

        max_result = conn.execute(
            sa.text("SELECT max_storage_gb FROM prune_config;")
        )
        max_size_gb = max_result.scalar_one()
        
        if max_size_gb is None or max_size_gb <= 0:
            logger.info("No storage limit set.")
            return 0
            
        max_size = max_size_gb * (1024**3)
        total_bytes_to_remove = current_size - max_size
        
        logger.info("Current size: %d MB; Max size: %d MB", current_size / (1024**2), max_size / (1024**2))
        
        if total_bytes_to_remove <= 0:
            logger.info("No need to prune.")
            return 0

        logger.info("Attempting to prune %d MB", max(0, total_bytes_to_remove / (1024**2)))

        # Get prunable demos (oldest first, no analysis data)
        result = conn.execute(
            sa.text(
                """
                SELECT session_id, demo_size FROM demo_sessions
                WHERE active = false
                AND open = false
                AND pruned = false
                AND session_id NOT IN (SELECT session_id FROM analysis)
                ORDER BY created_at ASC
                LIMIT 1000
                """
            )
        )

        prunable_demos = result.all()

        # Check which blobs actually exist (with timeout)
        # Check both buckets since demos can be in either during transition
        minio_blobs = list_objects_with_timeout(minio_client, "demoblobs")
        minio_blobs.update(list_objects_with_timeout(minio_client, "rawblobs"))
        
        session_ids_to_remove = []
        bytes_saved = 0

        for row in prunable_demos:
            session_id = row[0]
            demo_size = row[1] or 0
            
            # Use DB size as estimate; blob existence checked via minio_blobs dict
            # Check both compressed and raw formats
            if demo_blob_name(session_id) not in minio_blobs and raw_blob_name(session_id) not in minio_blobs:
                continue
                
            session_ids_to_remove.append(session_id)
            bytes_saved += demo_size
            
            if bytes_saved >= total_bytes_to_remove:
                break

        if len(session_ids_to_remove) == 0:
            logger.warning("No demos to prune, but we're over the limit!")
            return 0

        conn.execute(
            sa.text(
                """
                UPDATE demo_sessions
                SET pruned = true
                WHERE session_id IN :session_ids_to_remove;
                """
            ),
            {"session_ids_to_remove": tuple(session_ids_to_remove)},
        )
        conn.commit()
        logger.info("Marked %d demos for pruning.", len(session_ids_to_remove))
        
        return len(session_ids_to_remove)


def cleanup_pruned_demos(engine: Engine, minio_client: Minio) -> tuple[int, int]:
    """Remove blobs for pruned or orphaned sessions.
    
    Returns (removed_demos, removed_jsons) counts.
    """
    logger.info("Checking for orphaned demos...")
    
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT session_id FROM demo_sessions WHERE pruned = false;")
        )
        ids_in_db = [row[0] for row in result.all()]

        # Get current blobs with timeout (demoblobs only - rawblobs will be compressed)
        minio_demoblobs_dict = list_objects_with_timeout(minio_client, "demoblobs")
        minio_jsonblobs_dict = list_objects_with_timeout(minio_client, "jsonblobs")

        # Remove known valid blobs from the dicts
        for session_id in ids_in_db:
            demo_blob = demo_blob_name(session_id)
            json_blob = json_blob_name(session_id)
            minio_demoblobs_dict.pop(demo_blob, None)
            minio_jsonblobs_dict.pop(json_blob, None)

        # Check prune ratio safety
        ratio_result = conn.execute(
            sa.text("SELECT max_prune_ratio FROM prune_config;")
        )
        max_prune_ratio = ratio_result.scalar_one()
        
        if len(minio_demoblobs_dict) > len(ids_in_db) * max_prune_ratio and max_prune_ratio >= 0:
            logger.warning(
                "Too many orphaned demo blobs: %d (%.1f%%) found, but limit set to %.0f (%.1f%%). "
                "Refusing to clean up because something probably broke.",
                len(minio_demoblobs_dict),
                len(minio_demoblobs_dict) / max(len(ids_in_db), 1) * 100,
                max(len(ids_in_db) * max_prune_ratio, 0),
                max_prune_ratio * 100,
            )
            return (0, 0)

        if max_prune_ratio < 0:
            max_prune_ratio = abs(max_prune_ratio)
            logger.info("Orphaned demo cleanup forced by config. Setting back to %.2f", max_prune_ratio)
            conn.execute(
                sa.text("UPDATE prune_config SET max_prune_ratio = :mpr;"),
                {"mpr": max_prune_ratio},
            )
            conn.commit()

        # Remove orphaned blobs with timeout protection
        removed_demos = 0
        for blob in list(minio_demoblobs_dict.values()):  # Copy to avoid modification during iteration
            if remove_object_with_timeout(minio_client, "demoblobs", blob.object_name):
                logger.info("Removing orphaned demo %s", blob.object_name)
                removed_demos += 1

        removed_jsons = 0
        for blob in list(minio_jsonblobs_dict.values()):
            if remove_object_with_timeout(minio_client, "jsonblobs", blob.object_name):
                logger.info("Removing orphaned json %s", blob.object_name)
                removed_jsons += 1

    logger.info("Removed %d orphaned demos and %d orphaned jsons.", removed_demos, removed_jsons)

    # Clean up orphaned files in /media folder
    _cleanup_media_orphans(ids_in_db)

    return (removed_demos, removed_jsons)


def _cleanup_media_orphans(session_ids_in_db: list[str]) -> None:
    """Remove demo files from ~/media/demos that have no record in the DB."""
    from masterbase.lib import DEMOS_PATH

    if not os.path.isdir(DEMOS_PATH):
        return

    valid_names = {f"{sid}.dem" for sid in set(session_ids_in_db)}
    removed = 0
    for filename in os.listdir(DEMOS_PATH):
        if filename in valid_names:
            continue
        filepath = os.path.join(DEMOS_PATH, filename)
        try:
            if os.path.isfile(filepath):
                os.remove(filepath)
                logger.info("Removed orphaned media file %s", filename)
                removed += 1
        except OSError as e:
            logger.error("Failed to remove %s: %s", filepath, e)
    if removed:
        logger.info("Cleaned up %d orphaned files from %s", removed, DEMOS_PATH)
