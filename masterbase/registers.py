"""Functions that register state for the application."""

from typing import cast

from litestar import Litestar
from minio import Minio
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from masterbase.lib import make_db_uri, make_s3_svc


def get_s3_connection(app: Litestar) -> Minio:
    """Initialize and mount S3-compatible client, if not already attached."""
    if not getattr(app.state, "s3", None):
        s3 = make_s3_svc()
        if not s3.bucket_exists("demos"):
            s3.make_bucket("demos", "us-east-1")
        app.state.s3 = s3

    return cast("Minio", app.state.s3)


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


startup_registers = (get_db_connection, get_async_db_connection, get_s3_connection)
shutdown_registers = (close_db_connection, close_async_db_connection)
