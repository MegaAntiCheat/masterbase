"""Functions that register state for the application."""

from typing import cast

from litestar import Litestar
from minio import Minio
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from masterbase.lib import (
    make_db_uri,
    make_minio_client,
    cleanup_hung_sessions,
    cleanup_pruned_demos,
    prune_if_necessary
)


def get_minio_connection(app: Litestar) -> Minio:
    """Initialize and mount S3-compatible client, if not already attached."""
    if not getattr(app.state, "minio_client", None):
        minio_client = make_minio_client()
        if not minio_client.bucket_exists("demoblobs"):
            minio_client.make_bucket("demoblobs", "us-east-1")
        if not minio_client.bucket_exists("jsonblobs"):
            minio_client.make_bucket("jsonblobs", "us-east-1")
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
    """Close the db connection stored in the application State object."""
    if getattr(app.state, "async_engine", None):
        await cast("AsyncEngine", app.state.async_engine).dispose()

def boot_cleanup(app: Litestar) -> None:
    """Cleanup the database on boot."""
    engine = app.state.engine
    minio_client = app.state.minio_client

    cleanup_hung_sessions(engine)
    prune_if_necessary(engine, minio_client)
    cleanup_pruned_demos(engine, minio_client)

startup_registers = (get_db_connection, get_async_db_connection, get_minio_connection, boot_cleanup)
shutdown_registers = (close_db_connection, close_async_db_connection)
