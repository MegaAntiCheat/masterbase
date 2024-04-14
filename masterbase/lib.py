"""Library code for application."""

import logging
import os
import secrets
from datetime import datetime, timezone
from typing import IO, Any, Generator
from uuid import uuid4
from xml.etree import ElementTree

import requests
import sqlalchemy as sa
from litestar import WebSocket
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

DEMOS_PATH = os.path.expanduser(os.path.join("~/media", "demos"))
os.makedirs(DEMOS_PATH, exist_ok=True)

LATE_BYTES_START = 0x420
LATE_BYTES_end = 0x430


def make_db_uri(is_async: bool = False) -> str:
    """Correctly make the database URi."""
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    prefix = "postgresql"
    if is_async:
        prefix = f"{prefix}+asyncpg"

    return f"{prefix}://{user}:{password}@{host}:{port}/demos"


def make_demo_path(session_id: str) -> str:
    """Make the demo path for the current session."""
    return os.path.join(DEMOS_PATH, f"{session_id}.dem")


def steam_id_from_api_key(engine: Engine, api_key: str) -> str:
    """Resolve a steam ID from an  API key."""
    with engine.connect() as conn:
        steam_id = conn.execute(
            # should use a CTE here...
            sa.text("SELECT steam_id from api_keys where api_key = :api_key;"),
            {"api_key": api_key},
        ).scalar_one()

    return steam_id


async def async_steam_id_from_api_key(engine: AsyncEngine, api_key: str) -> str:
    """Resolve a steam ID from an  API key."""
    async with engine.connect() as conn:
        result = await conn.execute(
            # should use a CTE here...
            sa.text("SELECT steam_id from api_keys where api_key = :api_key;"),
            {"api_key": api_key},
        )
        steam_id = result.scalar_one()

    return steam_id


def _get_latest_session_id(engine: Engine, steam_id: str) -> str | None:
    """Get the latest session_id for a user."""
    with engine.connect() as conn:
        latest_session_id = conn.execute(
            # should use a CTE here...
            sa.text(
                "SELECT session_id FROM demo_sessions WHERE start_time = (SELECT MAX(start_time) FROM demo_sessions WHERE steam_id = :steam_id);"  # noqa
            ),
            {"steam_id": steam_id},
        ).scalar_one_or_none()

    return latest_session_id


def generate_uuid4_int() -> int:
    """Seems useless, but makes testing easier."""
    return uuid4().int


def generate_api_key() -> str:
    """Generate an API key."""
    return f"MB-{secrets.token_urlsafe(32)}"


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


async def check_is_active(engine: AsyncEngine, steam_id: str) -> bool:
    """Determine if a user is in an active session."""
    sql = "SELECT * FROM demo_sessions WHERE steam_id = :steam_id and active = true;"
    params = {"steam_id": steam_id}

    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(sql),
            params,
        )

        data = result.all()
        is_active = bool(data)

        return is_active


async def check_is_open(engine: AsyncEngine, steam_id: str, session_id: str) -> bool:
    """Determine if a user is streaming data."""
    sql = "SELECT open FROM demo_sessions WHERE steam_id = :steam_id and session_id = :session_id;"
    params = {"steam_id": steam_id, "session_id": session_id}

    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(sql),
            params,
        )

        data = result.scalar_one_or_none()
        is_open = bool(data)

        return is_open


async def set_open_true(engine: AsyncEngine, steam_id: str, session_id: str) -> None:
    """Set `open` to true, indicating the user is streaming data."""
    sql = "UPDATE demo_sessions SET open = true WHERE steam_id = :steam_id and session_id = :session_id;"
    params = {"steam_id": steam_id, "session_id": session_id}

    async with engine.connect() as conn:
        await conn.execute(
            sa.text(sql),
            params,
        )
        await conn.commit()


async def set_open_false(engine: AsyncEngine, session_id: str) -> None:
    """Set `open` to false, indicating the user not streaming data."""
    sql = "UPDATE demo_sessions SET open = false WHERE session_id = :session_id;"
    params = {"session_id": session_id}

    async with engine.connect() as conn:
        await conn.execute(
            sa.text(sql),
            params,
        )
        await conn.commit()


async def check_analyst(engine: AsyncEngine, steam_id: str) -> bool:
    """Determine if a user is in an analyst."""
    sql = """
        SELECT
            *
        FROM
            analyst_steam_ids
        WHERE
            steam_id = :steam_id
        ;
    """
    params = {"steam_id": steam_id}

    async with engine.connect() as conn:
        _result = await conn.execute(
            sa.text(sql),
            params,
        )

        result = _result.one_or_none()
        analyst = True if result is not None else False

        return analyst


async def session_closed(engine: AsyncEngine, session_id: str) -> bool:
    """Determine if a session is active."""
    sql = "SELECT active FROM demo_sessions WHERE session_id = :session_id;"
    params = {"session_id": session_id}

    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(sql),
            params,
        )

        data = result.scalar_one_or_none()
        closed = not data

        return closed


def start_session_helper(
    engine: Engine, steam_id: str, session_id: str, demo_name: str, fake_ip: str, map_str: str
) -> None:
    """Start a session and persist to DB."""
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                """INSERT INTO demo_sessions (
                    steam_id,
                    session_id,
                    demo_name,
                    active,
                    open,
                    start_time,
                    end_time,
                    fake_ip,
                    map,
                    steam_api_data,
                    ingested,
                    created_at,
                    updated_at
                ) VALUES (
                    :steam_id,
                    :session_id,
                    :demo_name,
                    :active,
                    :open,
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
                "steam_id": steam_id,
                "session_id": session_id,
                "demo_name": demo_name,
                "active": True,
                "open": False,
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


def _close_session_without_demo(engine: Engine, steam_id: str, current_time: datetime) -> None:
    """Close out a session in the DB."""
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                """UPDATE demo_sessions
                SET
                active = False,
                open = False,
                end_time = :end_time,
                updated_at = :updated_at
                WHERE
                active = True AND
                steam_id = :steam_id;"""
            ),
            {
                "steam_id": steam_id,
                "end_time": current_time.isoformat(),
                "updated_at": current_time.isoformat(),
            },
        )
        conn.commit()


def _close_session_with_demo(
    engine: Engine, steam_id: str, session_id: str, current_time: datetime, demo_path: str
) -> None:
    """Close out a session in the DB."""
    with engine.connect() as conn:
        size = os.stat(demo_path).st_size
        oid = conn.connection.lobject(mode="w", new_file=demo_path).oid  # type: ignore
        conn.execute(
            sa.text(
                """UPDATE demo_sessions
                SET
                active = False,
                open = False,
                end_time = :end_time,
                demo_oid = :demo_oid,
                demo_size = :demo_size,
                updated_at = :updated_at
                WHERE
                steam_id = :steam_id AND
                session_id = :session_id;"""
            ),
            {
                "steam_id": steam_id,
                "session_id": session_id,
                "end_time": current_time.isoformat(),
                "updated_at": current_time.isoformat(),
                "demo_size": size,
                "demo_oid": oid,
            },
        )
        conn.commit()


def close_session_helper(engine: Engine, steam_id: str, streaming_sessions: dict[WebSocket, IO]) -> str:
    """Properly close a session and return a summary message.

    Args:
        engine: Engine for the DB
        steam_id: steam id of the user
        streaming_sessions: dict of active sessions being streamed to

    Returns:
        status message on what happened
    """
    latest_session_id = _get_latest_session_id(engine, steam_id)
    if latest_session_id is None:
        return "User has never been in a session!"

    demo_path = make_demo_path(latest_session_id)
    demo_path_exists = os.path.exists(demo_path)

    current_time = datetime.now().astimezone(timezone.utc)

    if latest_session_id is None or not demo_path_exists:
        _close_session_without_demo(engine, steam_id, current_time)
        msg = "No active session found, closing anyway."

    elif latest_session_id is not None and demo_path_exists:
        _close_session_with_demo(engine, steam_id, latest_session_id, current_time, demo_path)
        os.remove(demo_path)
        msg = "Active session was closed, demo inserted."

    # we found no session but did find a demo
    else:
        os.remove(demo_path)
        msg = f"Found orphaned session and demo at {demo_path} and removed."

    # remove session from active sessions
    to_pop = None
    for session, handle in streaming_sessions.items():
        handle_id = session_id_from_handle(handle)
        if handle_id == latest_session_id:
            to_pop = session

    if to_pop is not None:
        streaming_sessions.pop(to_pop)

    return msg


def late_bytes_helper(engine: Engine, steam_id: str, late_bytes: bytes, current_time: datetime) -> None:
    """Add late bytes to the DB."""
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                """UPDATE demo_sessions
                SET
                late_bytes = :late_bytes,
                updated_at = :updated_at
                WHERE
                steam_id = :steam_id
                AND updated_at = (
                    SELECT MAX(updated_at) FROM demo_sessions WHERE steam_id = :steam_id
                );"""
            ),
            {
                "steam_id": steam_id,
                "late_bytes": late_bytes,
                "updated_at": current_time.isoformat(),
            },
        )
        conn.commit()


def get_demo_size(engine: Engine, session_id: str) -> str:
    """Get the size of a demo."""
    sql = "SELECT demo_size FROM demo_sessions WHERE session_id = :session_id;"
    with engine.connect() as conn:
        size = conn.execute(sa.text(sql), dict(session_id=session_id)).scalar_one()

        return str(size)


def demodata_helper(engine: Engine, api_key: str, session_id: str) -> Generator[bytes, None, None]:
    """Yield demo data page by page."""
    sql = """
        SELECT pageno, data
        FROM pg_largeobject
        JOIN demo_sessions demo ON demo.demo_oid = pg_largeobject.loid
        WHERE demo.session_id = :session_id
        ORDER BY pageno;
    """
    with engine.connect() as conn:
        with conn.execution_options(stream_results=True, fetch_size=100) as stream_conn:
            result = stream_conn.execute(sa.text(sql), dict(session_id=session_id))

            for i, row in enumerate(result):
                # probably not the best check but always here...
                bytestream = row[1].tobytes()
                if i == 0:
                    sql = "SELECT late_bytes from demo_sessions where session_id = :session_id;"
                    late_bytes = conn.execute(sa.text(sql), dict(session_id=session_id)).scalar_one()
                    if late_bytes is None:
                        logger.info(f"Session {session_id} has no late_bytes!")
                        yield bytestream
                    else:
                        # bytesurgeon >:D
                        bytestream = bytestream[:LATE_BYTES_START] + late_bytes + bytestream[LATE_BYTES_end:]
                        yield bytestream
                else:
                    yield bytestream


def list_demos_helper(engine: Engine, api_key: str, page_size: int, page_number: int) -> list[dict[str, Any]]:
    """List all demos in the DB for a user with pagination."""
    offset = (page_number - 1) * page_size

    sql = """
    SELECT
        demo_name, session_id, map, start_time, end_time, demo_size
    FROM
        demo_sessions
    WHERE
        active = false
    LIMIT :page_size OFFSET :offset
    ;
    """

    with engine.connect() as conn:
        data = conn.execute(sa.text(sql), {"page_size": page_size, "offset": offset})

    return [row._asdict() for row in data.all()]


def check_steam_id_has_api_key(engine: Engine, steam_id: str) -> str | None:
    """Check that a given steam id has an API key or not."""
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT api_key FROM api_keys WHERE steam_id = :steam_id"), {"steam_id": steam_id}
        ).scalar_one_or_none()

        return result


def update_api_key(engine: Engine, steam_id: str, new_api_key: str) -> None:
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
