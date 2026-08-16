"""Functions that register state for the application."""

import logging
import os
from typing import cast

from litestar import Litestar
from minio import Minio
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from masterbase.taskengine import init_task_runner, start_task_runner, stop_task_runner
from masterbase.lib import make_db_uri, make_minio_client

logger = logging.getLogger(__name__)

DEVELOPER_API_KEY = "MB-Developer"
DEVELOPER_STEAM_ID = "0"


def manage_developer_key(app: Litestar) -> None:
    """Add or remove the developer API key based on DEVELOPMENT env var."""
    engine = app.state.engine
    development = bool(os.getenv("DEVELOPMENT", "false").lower() == "true")

    with engine.connect() as conn:
        existing = conn.execute(
            __import__("sqlalchemy").text(
                "SELECT api_key FROM api_keys WHERE steam_id = :steam_id"
            ),
            {"steam_id": DEVELOPER_STEAM_ID},
        ).one_or_none()

        if development:
            if existing is None:
                conn.execute(
                    __import__("sqlalchemy").text(
                        "INSERT INTO api_keys (steam_id, api_key, created_at, updated_at, oid_hash) VALUES (:steam_id, :api_key, NOW(), NOW(), :oid_hash)"
                    ),
                    {
                        "steam_id": DEVELOPER_STEAM_ID,
                        "api_key": DEVELOPER_API_KEY,
                        "oid_hash": "developer",
                    },
                )
                conn.commit()
                logger.info("Added developer API key '%s' for steamid '%s'", DEVELOPER_API_KEY, DEVELOPER_STEAM_ID)
            else:
                logger.debug("Developer API key already exists")
        else:
            if existing is not None:
                conn.execute(
                    __import__("sqlalchemy").text(
                        "DELETE FROM api_keys WHERE steam_id = :steam_id"
                    ),
                    {"steam_id": DEVELOPER_STEAM_ID},
                )
                conn.commit()
                logger.info("Removed developer API key for steamid '%s' (DEVELOPMENT=false)", DEVELOPER_STEAM_ID)
            else:
                logger.debug("No developer API key to remove")


def get_minio_connection(app: Litestar) -> Minio:
    """Initialize and mount S3-compatible client, if not already attached.
    
    Buckets:
    - rawblobs: Raw .dem files (uploaded at session close)
    - demoblobs: Compressed .dem.tar.xz files (created by background compression)
    - jsonblobs: Analysis result JSON files
    """
    if not getattr(app.state, "minio_client", None):
        minio_client = make_minio_client()
        for bucket in ("rawblobs", "demoblobs", "jsonblobs"):
            if not minio_client.bucket_exists(bucket):
                minio_client.make_bucket(bucket, "us-east-1")
        app.state.minio_client = minio_client

    return cast(Minio, app.state.minio_client)


def get_db_connection(app: Litestar) -> Engine:
    """Get the db engine.

    If it doesn't exist, creates it and saves it in on the application state object
    """
    if not getattr(app.state, "engine", None):
        app.state.engine = create_engine(make_db_uri(), pool_pre_ping=True)
    return cast("Engine", app.state.engine)


def close_db_connection(app: Litestar) -> None:
    """Close the db connection stored in the application State object."""
    if getattr(app.state, "engine", None):
        cast("Engine", app.state.engine).dispose()


def get_async_db_connection(app: Litestar) -> AsyncEngine:
    """Get the async db engine.

    If it doesn't exist, creates it and saves it in on the application state object
    """
    if not getattr(app.state, "async_engine", None):
        app.state.async_engine = create_async_engine(make_db_uri(is_async=True), pool_pre_ping=True)
    return cast("AsyncEngine", app.state.async_engine)


async def close_async_db_connection(app: Litestar) -> None:
    """Close the async db connection stored in the application State object."""
    if getattr(app.state, "async_engine", None):
        await cast("AsyncEngine", app.state.async_engine).dispose()


def init_background_cleanup(app: Litestar) -> None:
    """Initialize and start the background cleanup runner.
    
    Cleanup runs asynchronously in a daemon thread, so startup is not blocked.
    The first cleanup cycle runs immediately in the background thread.
    """
    engine = app.state.engine
    minio_client = app.state.minio_client
    
    init_task_runner(engine, minio_client)
    start_task_runner()


def shutdown_background_cleanup(app: Litestar) -> None:
    """Stop the background cleanup runner on shutdown."""
    stop_task_runner()


startup_registers = (get_db_connection, get_async_db_connection, get_minio_connection, manage_developer_key, init_background_cleanup)
shutdown_registers = (shutdown_background_cleanup, close_db_connection, close_async_db_connection)
