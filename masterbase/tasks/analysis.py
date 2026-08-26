"""Analysis task handler."""

import io
import json
import logging
import os
import shutil
import subprocess

from minio import Minio, S3Error

from masterbase.lib import demo_blob_name, json_blob_name, raw_blob_name
from masterbase.tasks.handlers import TaskHandler

TASK_ANALYZE = "analyze"

logger = logging.getLogger(__name__)


class AnalyzeTask(TaskHandler):
    """Handler for analysis tasks."""
    
    task_type = TASK_ANALYZE

    @classmethod
    def run(cls, minio_client: Minio, engine, session_id: str) -> str | None:
        """Execute analysis by delegating to analyze_demo()."""
        return analyze_demo(minio_client, engine, session_id)


def analyze_demo(minio_client: Minio, engine, session_id: str) -> str | None:
    """Download demo, run analysis binary, ingest results.

    Steps:
    1. Download demo (from demoblobs if available, else rawblobs)
    2. Write to temp folder
    3. Execute analysis binary via subprocess
    4. Upload analysis JSON to jsonblobs
    5. Ingest results into DB
    6. Cleanup temp folder

    Returns:
        Error message or None on success
    """
    from masterbase.tasks import (
        ANALYSIS_BINARY, ANALYSIS_TIMEOUT, ANALYSIS_DIR,
    )
    
    if not os.path.exists(ANALYSIS_BINARY):
        return "Analysis binary not found - ensure ANALYSIS_BINARY is set correctly in .env"

    from masterbase.analysis import submit_analysis as ingest_analysis

    # Check if compressed demo is available
    compressed_available = False
    try:
        minio_client.stat_object("demoblobs", demo_blob_name(session_id))
        compressed_available = True
    except S3Error:
        pass

    work_dir = os.path.join(ANALYSIS_DIR, session_id)
    try:
        os.makedirs(work_dir, exist_ok=True)

        # Download demo: prefer compressed (demoblobs), fall back to rawblobs
        demo_path = os.path.join(work_dir, f"{session_id}.dem")
        downloaded = False

        if compressed_available:
            compressed_name = demo_blob_name(session_id)
            try:
                response = minio_client.get_object("demoblobs", compressed_name)
                try:
                    compressed_data = response.read()
                finally:
                    response.close()
                # Decompress tar.xz
                import tarfile as tf_mod
                import io as io_mod
                with tf_mod.open(fileobj=io_mod.BytesIO(compressed_data), mode="r:xz") as tar:
                    member = tar.extractfile(f"{session_id}.dem")
                    if member:
                        with open(demo_path, "wb") as f:
                            shutil.copyfileobj(member, f)
                        downloaded = True
            except S3Error:
                pass

        if not downloaded:
            # Fall back to rawblobs
            raw_name = raw_blob_name(session_id)
            try:
                response = minio_client.get_object("rawblobs", raw_name)
                try:
                    with open(demo_path, "wb") as f:
                        shutil.copyfileobj(response, f)
                finally:
                    response.close()
                downloaded = True
            except S3Error as e:
                return f"Failed to download demo: {e}"

        if not downloaded:
            return "Demo not found in demoblobs or rawblobs"

        # Run analysis binary
        output_path = os.path.join(work_dir, "analysis.json")
        try:
            result = subprocess.run(
                [ANALYSIS_BINARY, "-q", "-i", demo_path, "-o", output_path],
                capture_output=True,
                timeout=ANALYSIS_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            return f"Analysis timed out after {ANALYSIS_TIMEOUT}s"
        except FileNotFoundError:
            return f"Analysis binary not found: {ANALYSIS_BINARY}"

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            return f"Analysis exited with code {result.returncode}: {stderr}"

        # Read analysis JSON
        try:
            with open(output_path, "r") as f:
                analysis_data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            return f"Failed to read analysis output: {e}"

        # Upload to jsonblobs for archival
        json_name = json_blob_name(session_id)
        json_bytes = json.dumps(analysis_data).encode("utf-8")
        try:
            minio_client.put_object(
                "jsonblobs",
                json_name,
                data=io.BytesIO(json_bytes),
                length=len(json_bytes),
            )
        except S3Error as e:
            logger.warning("Failed to upload analysis JSON to MinIO: %s", e)

        # Ingest into DB
        from masterbase.models import Analysis
        try:
            analysis_obj = Analysis(**analysis_data)
        except Exception as e:
            return f"Invalid analysis data: {e}"

        error = ingest_analysis(minio_client, engine, session_id, analysis_obj)
        if error:
            return error

        logger.info("Analyzed demo %s successfully", session_id)
        return None

    finally:
        # Cleanup temp folder
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception as e:
            logger.warning("Failed to cleanup analysis temp dir %s: %s", work_dir, e)
