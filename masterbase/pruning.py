"""Session cleanup and pruning."""

import math
import logging

import sqlalchemy as sa
from minio import Minio, S3Error
from sqlalchemy import Engine
from masterbase.lib import demo_blob_name, json_blob_name

logger = logging.getLogger(__name__)

# This function is only meant to run on boot!
def cleanup_hung_sessions(engine: Engine) -> None:
    """Remove any sessions that were left open/active after shutdown."""
    logger.info("Checking for hanging sessions...")
    with engine.connect() as conn:
        result = conn.execute(
            sa.text(  # We have to delete reports first because of the REFERENCES constraint
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


# This function is only meant to run on boot!
def prune_if_necessary(engine: Engine, minio_client: Minio) -> bool:
    """Mark sessions as pruned so the specificed amount of free space is available."""
    logger.info("Checking if we need to prune demos...")
    current_size = get_total_storage_usage(minio_client)

    with engine.connect() as conn:
        max_result = conn.execute(
            sa.text(
                """
                SELECT value from config WHERE setting = 'max_storage_gb';
                """
            )
        )
        max_size_gb = int(max_result.scalar_one())
        if max_size_gb is None or max_size_gb <= 0:
            logger.warning("No storage limit set, enjoy filling your disk!")
            return False
        max_size = max_size_gb * (1024**3)
        total_bytes_to_remove = current_size - max_size
        logger.info("Current size: %d MB; Max size: %d MB", current_size / (1024**2), max_size / (1024**2))
        if total_bytes_to_remove <= 0:
            logger.info("No need to prune.")
            return False

        logger.info("Attempting to prune %d MB", max(0, total_bytes_to_remove / (1024**2)))

        # get the oldest demos that don't have any detections
        # we allow demos that have already been pruned in case we somehow end up in a state
        # where a demo is marked as pruned but its blob remains.
        result = conn.execute(
            sa.text(
                """
                SELECT session_id FROM demo_sessions
                WHERE active = false
                AND open = false
                AND session_id NOT IN (SELECT session_id FROM analysis)
                ORDER BY created_at ASC
                """
            )
        )

        prunable_demos_oldest_first = [row[0] for row in result.all()]

        minio_demoblobs_dict = {blob.object_name: blob for blob in minio_client.list_objects("demoblobs")}
        session_ids_to_remove = []
        bytes_saved = 0

        # prune just enough so we're in our space budget
        for session_id in prunable_demos_oldest_first:
            blob = minio_demoblobs_dict.get(demo_blob_name(session_id))
            if blob is None:
                # already pruned, do not count
                continue
            session_ids_to_remove.append(session_id)
            bytes_saved += blob.size
            if bytes_saved >= total_bytes_to_remove:
                break

        if len(session_ids_to_remove) == 0:
            logger.warning("No demos to prune, but we're over the limit!")
            return False

        # mark as pruned
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
        # pruned demo blobs will be deleted by cleanup_orphaned_demos, which runs after this on boot
    return True


# This function is only meant to run on boot!
def cleanup_pruned_demos(engine: Engine, minio_client: Minio) -> None:
    """Remove blobs for pruned or deleted sessions."""
    logger.info("Checking for orphaned demos.")
    with engine.connect() as conn:
        result = conn.execute(
            sa.text(
                """
                SELECT session_id FROM demo_sessions WHERE pruned = false;
                """
            )
        )
        ids_in_db = [row[0] for row in result.all()]
        minio_demoblobs_dict = {blob.object_name: blob for blob in minio_client.list_objects("demoblobs")}
        minio_jsonblobs_dict = {blob.object_name: blob for blob in minio_client.list_objects("jsonblobs")}

        for session_id in ids_in_db:
            demo_blob = demo_blob_name(session_id)
            json_blob = json_blob_name(session_id)
            if minio_demoblobs_dict.get(demo_blob) is not None:
                minio_demoblobs_dict.pop(demo_blob)
            if minio_jsonblobs_dict.get(json_blob) is not None:
                minio_jsonblobs_dict.pop(json_blob)

        # dicts now contain only orphaned blobs

        ratio_result = conn.execute(
            sa.text(
                """
                SELECT value from config WHERE setting = 'max_prune_ratio';
                """
            )
        )
        # If we're gonna wipe more than max_prune_ratio (default 0.05) of the blobs, something is probably very wrong.
        # Setting this to negative will perform a one-time prune regardless of ratio.
        max_prune_ratio = float(ratio_result.scalar_one())
        if len(minio_demoblobs_dict) > len(ids_in_db) * max_prune_ratio and max_prune_ratio >= 0:
            logger.warning(
                "Too many orphaned demo blobs: %d (%f%%) found, but limit set to %d (%f%%). "
                "Refusing to clean up because something probably broke.",
                len(minio_demoblobs_dict),
                len(minio_demoblobs_dict) / len(ids_in_db) * 100,
                math.floor(len(ids_in_db) * max_prune_ratio),
                max_prune_ratio * 100,
            )
            return

        if max_prune_ratio < 0:
            max_prune_ratio = abs(max_prune_ratio)
            logger.info("Orphaned demo cleanup forced by config. Setting back to %f", max_prune_ratio)
            conn.execute(
                sa.text(
                    """
                    UPDATE prune_config
                    SET max_prune_ratio = :max_prune_ratio;
                    """
                ),
                {"max_prune_ratio": max_prune_ratio},
            )
            conn.commit()

        print_limit = 20
        removed_demos = 0
        removed_jsons = 0
        for blob in minio_demoblobs_dict.values():
            if removed_demos < print_limit:
                logger.info("Removing orphaned demo %s", blob.object_name)
            minio_client.remove_object("demoblobs", blob.object_name)
            removed_demos += 1
        if removed_demos > print_limit:
            logger.info("Removing %d more orphaned demos...", removed_demos - print_limit)
        for blob in minio_jsonblobs_dict.values():
            if removed_jsons < print_limit:
                logger.info("Removing orphaned json %s", blob.object_name)
            minio_client.remove_object("jsonblobs", blob.object_name)
            removed_jsons += 1
        if removed_jsons > print_limit:
            logger.info("Removing %d more orphaned jsons...", removed_jsons - print_limit)



def get_total_storage_usage(minio_client: Minio) -> int:
    """Get the total storage used by all buckets in bytes."""
    try:
        buckets = minio_client.list_buckets()
        total_size = 0

        for bucket in buckets:
            objects = minio_client.list_objects(bucket.name, recursive=True)
            bucket_size = sum(obj.size for obj in objects)
            total_size += bucket_size

        return total_size
    except S3Error as exc:
        print("Error occurred:", exc)
        return -1
