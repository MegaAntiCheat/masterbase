"""Library code for application."""

import os
from datetime import datetime, timezone
from typing import IO
from uuid import uuid4
from xml.etree import ElementTree

import requests
import sqlalchemy as sa
from litestar import WebSocket
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

DEMOS_PATH = os.path.expanduser(os.path.join("~/media", "demos"))
os.makedirs(DEMOS_PATH, exist_ok=True)


def make_db_uri(is_async: bool = False) -> str:
    """Correctly make the database URi."""
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "8050")
    prefix = "postgresql"
    if is_async:
        prefix = f"{prefix}+asyncpg"

    return f"{prefix}://{user}:{password}@{host}:{port}/demos"


def make_demo_path(session_id: str) -> str:
    """Make the demo path for the current session."""
    return os.path.join(DEMOS_PATH, f"{session_id}.dem")


def _get_latest_session_id(engine: Engine, api_key: str) -> str | None:
    """Get the latest session_id for a user."""
    with engine.connect() as conn:
        latest_session_id = conn.execute(
            # should use a CTE here...
            sa.text(
                "SELECT session_id FROM demo_sessions WHERE start_time = (SELECT MAX(start_time) FROM demo_sessions WHERE api_key = :api_key);"  # noqa
            ),
            {"api_key": api_key},
        ).scalar_one_or_none()

    return latest_session_id


def generate_uuid4_int() -> int:
    """Seems useless, but makes testing easier."""
    return uuid4().int


def session_id_from_handle(handle: IO) -> str:
    """Get the session ID from a handle."""
    return os.path.splitext(os.path.basename(handle.name))[0]


async def check_key_exists(engine: AsyncEngine, api_key: str) -> bool:
    """Determine key existence."""
    async with engine.connect() as conn:
        result = await conn.execute(sa.text("SELECT * FROM api_keys WHERE api_key = :api_key"), {"api_key": api_key})
        data = result.all()
        if not data:
            return False

        return True


async def check_is_active(engine: AsyncEngine, api_key: str) -> bool:
    """Determine if a session is active."""
    sql = "SELECT * FROM demo_sessions WHERE api_key = :api_key and active = true;"
    params = {"api_key": api_key}

    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(sql),
            params,
        )

        data = result.all()
        is_active = bool(data)

        return is_active


def start_session_helper(
    engine: Engine, api_key: str, session_id: str, demo_name: str, fake_ip: str, map_str: str
) -> None:
    """Start a session and persist to DB."""
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                """INSERT INTO demo_sessions (
                    session_id,
                    api_key,
                    demo_name,
                    active,
                    start_time,
                    end_time,
                    fake_ip,
                    map,
                    steam_api_data,
                    ingested,
                    created_at,
                    updated_at
                ) VALUES (
                    :session_id,
                    :api_key,
                    :demo_name,
                    :active,
                    :start_time,
                    :end_time,
                    :fake_ip,
                    :map,
                    :steam_api_data,
                    :ingested,
                    :created_at,
                    :updated_at
                );
                """
            ),
            {
                "session_id": session_id,
                "api_key": api_key,
                "demo_name": demo_name,
                "active": True,
                "start_time": datetime.now().astimezone(timezone.utc).isoformat(),
                "end_time": None,
                "fake_ip": fake_ip,
                "map": map_str,
                "steam_api_data": None,
                "ingested": False,
                "created_at": datetime.now().astimezone(timezone.utc).isoformat(),
                "updated_at": datetime.now().astimezone(timezone.utc).isoformat(),
            },
        )
        conn.commit()


def _close_session_without_demo(engine: Engine, api_key: str, current_time: datetime) -> None:
    """Close out a session in the DB."""
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                """UPDATE demo_sessions
                SET
                active = False,
                end_time = :end_time,
                updated_at = :updated_at
                WHERE
                active = True AND
                api_key = :api_key;"""
            ),
            {
                "api_key": api_key,
                "end_time": current_time.isoformat(),
                "updated_at": current_time.isoformat(),
            },
        )
        conn.commit()


def _close_session_with_demo(
    engine: Engine, api_key: str, session_id: str, current_time: datetime, demo_path: str
) -> None:
    """Close out a session in the DB."""
    with engine.connect() as conn:
        oid = conn.connection.lobject(mode="w", new_file=demo_path).oid  # type: ignore
        conn.execute(
            sa.text(
                """UPDATE demo_sessions
                SET
                active = False,
                end_time = :end_time,
                demo_oid = :demo_oid,
                updated_at = :updated_at
                WHERE
                api_key = :api_key AND
                session_id = :session_id;"""
            ),
            {
                "api_key": api_key,
                "session_id": session_id,
                "end_time": current_time.isoformat(),
                "updated_at": current_time.isoformat(),
                "demo_oid": oid,
            },
        )
        conn.commit()


def close_session_helper(engine: Engine, api_key: str, streaming_sessions: dict[WebSocket, IO]) -> str:
    """Properly close a session and return a summary message.

    Args:
        engine: Engine for the DB
        api_key: api key of the user
        streaming_sessions: dict of active sessions being streamed to

    Returns:
        status message on what happened
    """
    latest_session_id = _get_latest_session_id(engine, api_key)
    if latest_session_id is None:
        return "User has never been in a session!"

    demo_path = make_demo_path(latest_session_id)
    demo_path_exists = os.path.exists(demo_path)

    current_time = datetime.now().astimezone(timezone.utc)

    if latest_session_id is None or not demo_path_exists:
        _close_session_without_demo(engine, api_key, current_time)
        msg = "No active session found, closing anyway."

    elif latest_session_id is not None and demo_path_exists:
        _close_session_with_demo(engine, api_key, latest_session_id, current_time, demo_path)
        os.remove(demo_path)
        msg = "Active session was closed, demo inserted."

    # we found no session but did find a demo
    else:
        os.remove(demo_path)
        msg = f"Found orphaned session and demo at {demo_path} and removed."

    # remove session from active sessions
    for session, handle in streaming_sessions.items():
        if session_id_from_handle(handle) == latest_session_id:
            streaming_sessions.pop(session)

    return msg


def late_bytes_helper(engine: Engine, api_key: str, late_bytes: bytes, current_time: datetime) -> None:
    """Add late bytes to the DB."""
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                """UPDATE demo_sessions
                SET
                late_bytes = :late_bytes,
                updated_at = :updated_at
                WHERE
                api_key = :api_key
                AND updated_at = (
                    SELECT MAX(updated_at) FROM demo_sessions WHERE api_key = :api_key
                );"""
            ),
            {
                "api_key": api_key,
                "late_bytes": late_bytes,
                "updated_at": current_time.isoformat(),
            },
        )
        conn.commit()


def check_steam_id_has_api_key(engine: Engine, steam_id: str) -> str | None:
    """Check that a given steam id has an API key or not."""
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT api_key FROM api_keys WHERE steam_id = :steam_id"), {"steam_id": steam_id}
        ).scalar_one_or_none()

        return result


def update_api_key(engine: Engine, steam_id: str, new_api_key) -> None:
    """Update an API key."""
    with engine.connect() as conn:
        conn.execute(
            sa.text("UPDATE api_keys SET api_key = :new_api_key WHERE steam_id = :steam_id"),
            {"steam_id": steam_id, "new_api_key": new_api_key},
        )
        conn.commit()


def check_steam_id_is_beta_tester(engine: Engine, steam_id: str) -> bool:
    """Check that a given steam id has an API key or not."""
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT * FROM beta_tester_steam_ids WHERE steam_id = :steam_id"), {"steam_id": steam_id}
        ).one_or_none()

        return bool(result)


def provision_api_key(engine: Engine, steam_id: str, api_key: str) -> None:
    """Provision an API key."""
    with engine.connect() as conn:
        created_at = datetime.now().astimezone(timezone.utc).isoformat()
        updated_at = created_at
        conn.execute(
            sa.text(
                """INSERT INTO api_keys (
                    steam_id, api_key, created_at, updated_at
                    ) VALUES (
                        :steam_id, :api_key, :created_at, :updated_at);"""
            ),
            {"steam_id": steam_id, "api_key": api_key, "created_at": created_at, "updated_at": updated_at},
        )
        conn.commit()


def is_limited_account(steam_id: str) -> bool:
    """Check if the account is limited or not."""
    response = requests.get(f"https://steamcommunity.com/profiles/{steam_id}?xml=1")
    tree = ElementTree.fromstring(response.content)
    for element in tree:
        if element.tag == "isLimitedAccount":
            limited = bool(int(str(element.text)))
            return limited

    raise ValueError(f"Could not determine if {steam_id} is limited!")
