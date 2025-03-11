"""Library code for application."""

import hashlib
import io
import json
import logging
import math
import os
import secrets
import socket
from datetime import datetime, timezone
from queue import Queue
from threading import Thread
from typing import IO, Any, BinaryIO, Generator, cast
from uuid import uuid4

import sqlalchemy as sa
from litestar import WebSocket
from minio import Minio, S3Error
from minio.datatypes import Object as BlobStat
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncEngine

from masterbase.anomaly import DetectionState
from masterbase.models import Analysis

logger = logging.getLogger(__name__)

DEMOS_PATH = os.path.expanduser(os.path.join("~/media", "demos"))
os.makedirs(DEMOS_PATH, exist_ok=True)

LATE_BYTES_START = 0x420
LATE_BYTES_END = 0x430


def resolve_hostname(hostname: str) -> str:
    """Resolve a hostname to an IP."""
    return socket.gethostbyname(hostname)


def make_db_uri(is_async: bool = False) -> str:
    """Correctly make the database URI."""
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    prefix = "postgresql"
    if is_async:
        prefix = f"{prefix}+asyncpg"

    return f"{prefix}://{user}:{password}@{host}:{port}/demos"


def make_minio_client(is_secure: bool = False) -> Minio:
    """Create and return an S3-compatible client handle."""
    host, port, access_key, secret_key = map(
        os.getenv, ("MINIO_HOST", "MINIO_PORT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY")
    )
    return Minio(f"{host}:{port}", access_key=access_key, secret_key=secret_key, secure=is_secure)


def db_export_chunks(engine: Engine, table: str) -> Generator[bytes, None, None]:
    """Export the given table as an iterable of csv chunks."""

    class Shunt:
        def write(self, data):
            queue.put(data)

    shunt = Shunt()
    queue: Queue = Queue()

    shunt = Shunt()

    def worker():
        try:
            with engine.connect() as txn:
                cursor = txn.connection.dbapi_connection.cursor()
                cursor.copy_expert(f"COPY {table} TO STDOUT DELIMITER ',' CSV HEADER", shunt)
                queue.put(b"")
        except Exception as err:
            queue.put(err)

    Thread(target=worker).start()
    for x in iter(queue.get, b""):
        if isinstance(x, Exception):
            raise x
        yield x


class ConcatStream:
    """Concat multiple filestreams."""

    def __init__(self, *streams: IO[bytes]) -> None:
        """Initialize the ConcatStream with multiple streams."""
        self.streams = iter(streams)
        self.current: IO[bytes] | None = next(self.streams, None)

    def read(self, size: int = -1) -> bytes:
        """Read from the concatenated streams."""
        if self.current is None:
            return b""

        if size < 0:
            data = bytearray()
            data += self.current.read()
            data += b"".join(s.read() for s in self.streams)
            self.current = None
            return data

        data = bytearray(size)
        head = 0
        while size > 0 and self.current is not None:
            read = self.current.read(size)
            if read == b"":
                self.current = next(self.streams, None)
            else:
                data[head : head + len(read)] = read
                head += len(read)
                size -= len(read)
        return bytes(data[:head])


class DemoSessionManager:
    """Helper class to facilitate access."""

    def __init__(self, session_id: str, detection_state: DetectionState) -> None:
        """Create a demo session manager.

        Args:
            session_id: ID of the session
            detection_state: DetectionState object to call
        """
        self.session_id = session_id
        self.detection_state = detection_state
        self.chunk_count = 0

    @property
    def demo_path(self) -> str:
        """Path of the demo for this session."""
        return demo_sink_path(self.session_id)

    def set_demo_handle(self, mode: str) -> None:
        """Open a handle with the mode at `self.demo_path`."""
        self.handle = open(self.demo_path, mode)

    def update(self, data: bytes) -> None:
        """Write data to both blob storage and state objects."""
        self.handle.write(data)
        self.detection_state.update(data)

    def disconnect(self) -> None:
        """Close objects and consolidate data."""
        self.handle.close()


SocketManagerMapType = dict[WebSocket, DemoSessionManager]


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


def ingest_demo(minio_client: Minio, engine: Engine, session_id: str):
    """Ingest a demo analysis from an analysis client."""
    blob_name = json_blob_name(session_id)
    try:
        raw_data = minio_client.get_object("jsonblobs", blob_name).read()
        decoded_data = raw_data.decode("utf-8")
        json_data = json.JSONDecoder().decode(decoded_data)
        data = Analysis.parse_obj(json_data)
    except S3Error as err:
        if err.code == "NoSuchKey":
            return "no analysis data found."
        else:
            return "unknown S3 error while looking up analysis data."
    except ValidationError:
        return "malformed analysis data."

    # Data preprocessing
    algorithm_counts = {}
    for detection in data.detections:
        key = (detection.player, detection.algorithm)
        if key not in algorithm_counts:
            algorithm_counts[key] = 0
        algorithm_counts[key] += 1

    # ensure the demo session is not already ingested
    is_ingested_sql = "SELECT ingested, active, open FROM demo_sessions WHERE session_id = :session_id;"

    # Wipe existing analysis data
    # (we want to be able to reingest a demo if necessary by manually setting ingested = false)
    wipe_analysis_sql = "DELETE FROM analysis WHERE session_id = :session_id;"
    wipe_reviews_sql = "DELETE FROM reviews WHERE session_id = :session_id;"

    # Insert the analysis data
    insert_sql = """\
        INSERT INTO analysis (
            session_id, target_steam_id, algorithm_type, detection_count, created_at
        ) VALUES (
            :session_id, :target_steam_id, :algorithm, :count, :created_at
        );
    """

    # Mark the demo as ingested
    mark_ingested_sql = "UPDATE demo_sessions SET ingested = true WHERE session_id = :session_id;"
    created_at = datetime.now().astimezone(timezone.utc).isoformat()

    with engine.connect() as conn:
        with conn.begin():
            command = conn.execute(
                sa.text(is_ingested_sql),
                {"session_id": session_id},
            )

            result = command.one_or_none()
            if result is None:
                conn.rollback()
                return "demo not found"
            if result.ingested is True:
                conn.rollback()
                return "demo already ingested"
            if result.active is True:
                conn.rollback()
                return "session is still active"
            if result.open is True:
                conn.rollback()
                return "session is still open"

            conn.execute(
                sa.text(wipe_analysis_sql),
                {"session_id": session_id},
            )
            conn.execute(
                sa.text(wipe_reviews_sql),
                {"session_id": session_id},
            )

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
                {"session_id": session_id},
            )
    return None


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
    minio_client: Minio,
    engine: Engine,
    steam_id: str,
    session_id: str,
    current_time: datetime,
    markov_score: float,
) -> None:
    """Close out a session in the DB, sink to MinIO."""
    sink_path = demo_sink_path(session_id)
    size = os.stat(sink_path).st_size
    with engine.connect() as conn:
        late_bytes = conn.execute(
            sa.text(
                """UPDATE demo_sessions
                SET
                active = False,
                open = False,
                end_time = :end_time,
                demo_size = :demo_size,
                markov_score = :markov_score,
                updated_at = :updated_at
                WHERE
                steam_id = :steam_id AND
                session_id = :session_id
                RETURNING late_bytes;
                """
            ),
            {
                "steam_id": steam_id,
                "session_id": session_id,
                "end_time": current_time.isoformat(),
                "updated_at": current_time.isoformat(),
                "demo_size": size,
                "markov_score": markov_score,
            },
        ).scalar_one()
        if late_bytes is not None:
            with open(sink_path, "rb") as sink:
                late = io.BytesIO(late_bytes)
                head = io.BytesIO(sink.read(LATE_BYTES_START))
                sink.seek(LATE_BYTES_END, os.SEEK_SET)
                minio_client.put_object(
                    "demoblobs",
                    demo_blob_name(session_id),
                    data=cast(BinaryIO, ConcatStream(head, late, sink)),
                    length=size,
                    metadata={"has_late_bytes": str(bool(late_bytes))},
                )
        else:
            minio_client.fput_object("demoblobs", demo_blob_name(session_id), file_path=sink_path)
        conn.commit()


def close_session_helper(
    minio_client: Minio,
    engine: Engine,
    steam_id: str,
    streaming_sessions:
    SocketManagerMapType,
    late_bytes: bytes | None
) -> str:
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

    # find session manager and socket/key...
    session_manager = None
    socket = None
    for _socket in streaming_sessions:
        if streaming_sessions[_socket].session_id == latest_session_id:
            session_manager = streaming_sessions[_socket]
            socket = _socket

    current_time = datetime.now().astimezone(timezone.utc)

    if session_manager is None:
        _close_session_without_demo(engine, steam_id, current_time)
        msg = "No active session found, closing anyway."
    else:
        if os.path.exists(session_manager.demo_path):
            if late_bytes is not None:
                late_bytes_msg = late_bytes_helper(engine, steam_id, late_bytes, current_time)
                if late_bytes_msg is not None:
                    return late_bytes_msg
            _close_session_with_demo(
                minio_client,
                engine,
                steam_id,
                latest_session_id,
                current_time,
                session_manager.detection_state.likelihood,
            )
            os.remove(session_manager.demo_path)
            msg = "Active session was closed, demo inserted."

        # we found no session but did find a demo
        else:
            os.remove(session_manager.demo_path)
            msg = f"Found orphaned session and demo at {session_manager.demo_path} and removed."

    # remove session from active sessions
    if socket is not None:
        streaming_sessions.pop(socket)

    return msg


def demo_blob_name(session_id: str) -> str:
    """Format the object name for a demo blob."""
    return f"{session_id}.dem"

def json_blob_name(session_id: str) -> str:
    """Format the object name for a json blob."""
    return f"{session_id}.json"

def demo_sink_path(session_id: str) -> str:
    """Format the media path for a demo blob."""
    return os.path.join(DEMOS_PATH, demo_blob_name(session_id))


def stat_demo_blob(minio_client: Minio, session_id: str) -> BlobStat | None:
    """Return information on the status of a given blob if it exists, else None."""
    try:
        return minio_client.stat_object("demos", demo_blob_name(session_id))
    except S3Error as err:
        if err.code == "NoSuchKey":
            return None
        else:
            raise


def late_bytes_helper(
    engine: Engine,
    steam_id: str,
    late_bytes: bytes,
    current_time: datetime,
) -> str | None:
    """Add late bytes to the database and blob storage.

    No-ops and returns an error message if late bytes are found or there are no active sessions.
    """
    with engine.connect() as conn:
        try:
            session_id, old_late_bytes = conn.execute(
                sa.text(
                    """SELECT session_id, late_bytes FROM demo_sessions
                    WHERE active = True
                    AND steam_id = :steam_id;
                    """,
                ),
                {"steam_id": steam_id},
            ).one()
        except NoResultFound:
            return "no active session"
        if session_id and old_late_bytes:
            return "already submitted"
        conn.execute(
            sa.text(
                """UPDATE demo_sessions
                    SET
                    late_bytes = :late_bytes,
                    updated_at = :updated_at
                    WHERE session_id = session_id;"""
            ),
            {
                "session_id": session_id,
                "late_bytes": late_bytes,
                "updated_at": current_time.isoformat(),
            },
        )
        conn.commit()
        return None


async def get_demo_size(engine: AsyncEngine, session_id: str) -> str:
    """Get the size of a demo."""
    sql = "SELECT demo_size FROM demo_sessions WHERE session_id = :session_id;"
    async with engine.connect() as conn:
        result = await conn.execute(sa.text(sql), dict(session_id=session_id))
        size = result.scalar_one()

        return str(size)


def list_demos_helper(
    engine: Engine, api_key: str, page_size: int, page_number: int, analyst: bool
) -> list[dict[str, Any]]:
    """List demos in the DB for a user with pagination."""
    requester_steam_id = steam_id_from_api_key(engine, api_key)
    offset = (page_number - 1) * page_size
    params: dict[str, Any] = {"page_size": page_size, "offset": offset}
    where = "active = false"
    if not analyst:
        where = f"{where} AND steam_id = :steam_id"
        params["steam_id"] = requester_steam_id

    sql = f"""
    SELECT
        steam_id, demo_name, session_id, map, start_time, end_time, demo_size
    FROM
        demo_sessions
    WHERE
        {where}
    ORDER BY
        start_time
    LIMIT :page_size OFFSET :offset
    ;
    """

    with engine.connect() as conn:
        data = conn.execute(sa.text(sql), params)

    rows = [row._asdict() for row in data.all()]
    # modify in place
    for row in rows:
        demo_steam_id = row["steam_id"]
        to_hash = f"{demo_steam_id}{requester_steam_id}"
        row["anonymous_id"] = hashlib.sha256(to_hash.encode()).hexdigest()
        row.pop("steam_id")

    return rows


def check_steam_id_has_api_key(engine: Engine, steam_id: str) -> tuple[str | None, str | None]:
    """Check that a given steam id has an API key or not."""
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT api_key, oid_hash FROM api_keys WHERE steam_id = :steam_id"), {"steam_id": steam_id}
        ).one_or_none()
        if result is None:
            api_key, oid_hash = None, None
        else:
            api_key, oid_hash = result

        return api_key, oid_hash


def update_api_key(engine: Engine, steam_id: str, new_api_key: str, oid_hash: str) -> None:
    """Update an API key."""
    with engine.connect() as conn:
        updated_at = datetime.now().astimezone(timezone.utc).isoformat()
        conn.execute(
            sa.text(
                "UPDATE api_keys SET api_key = :new_api_key, updated_at = :updated_at, oid_hash = :oid_hash WHERE steam_id = :steam_id"  # noqa
            ),
            {"steam_id": steam_id, "updated_at": updated_at, "new_api_key": new_api_key, "oid_hash": oid_hash},
        )
        conn.commit()


def provision_api_key(engine: Engine, steam_id: str, api_key: str, oid_hash: str) -> None:
    """Provision an API key."""
    with engine.connect() as conn:
        created_at = datetime.now().astimezone(timezone.utc).isoformat()
        updated_at = created_at
        conn.execute(
            sa.text(
                """INSERT INTO api_keys (
                    steam_id, api_key, created_at, updated_at, oid_hash
                    ) VALUES (
                        :steam_id, :api_key, :created_at, :updated_at, :oid_hash);"""
            ),
            {
                "steam_id": steam_id,
                "api_key": api_key,
                "created_at": created_at,
                "updated_at": updated_at,
                "oid_hash": oid_hash,
            },
        )
        conn.commit()


def add_loser(engine: Engine, steam_id: str) -> None:
    """Add a new loser to the database."""
    # see https://github.com/MegaAntiCheat/masterbase/issues/53
    with engine.connect() as conn:
        created_at = datetime.now().astimezone(timezone.utc).isoformat()
        updated_at = created_at
        conn.execute(
            sa.text(
                """INSERT INTO losers (
                    steam_id, created_at, updated_at
                    ) VALUES (
                        :steam_id, :created_at, :updated_at);"""
            ),
            {"steam_id": steam_id, "created_at": created_at, "updated_at": updated_at},
        )
        conn.commit()


def add_report(engine: Engine, session_id: str, target_steam_id: str, reason: str) -> None:
    """Submit a hackusation to the database."""
    # TODO: Eventually we need to enforce more rigorous checks
    with engine.connect() as txn:
        created_at = datetime.now().astimezone(timezone.utc).isoformat()
        txn.execute(
            sa.text(
                """INSERT INTO reports (
                    session_id, target_steam_id, created_at, reason
                ) VALUES (
                    :session_id, :target_steam_id, :created_at, :reason)
                ON CONFLICT (session_id, target_steam_id)
                DO UPDATE SET reason = EXCLUDED.reason, created_at = NOW();
                """
            ),
            {"target_steam_id": target_steam_id, "session_id": session_id, "created_at": created_at, "reason": reason},
        )
        txn.commit()


def check_is_loser(engine: Engine, steam_id: str) -> bool:
    """Determine if we have flagged account as a loser."""
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT COUNT(*) FROM losers WHERE steam_id = :steam_id"),
            {
                "steam_id": steam_id,
            },
        ).scalar_one_or_none()

        return bool(result)

def get_broadcasts(engine: Engine) -> list[dict[str, str]]:
    """Get the list of broadcasts."""
    with engine.connect() as conn:
        result = conn.execute(
           sa.text("SELECT * FROM broadcasts")
        )
        rows = [row._asdict() for row in result.all()]
        for row in rows:
            row["post_date"] = row.pop("created_at")
        return rows

# This function is only meant to run on boot!
def cleanup_hung_sessions(engine: Engine) -> None:
    """Remove any sessions that were left open/active after shutdown."""
    logger.info(f"Checking for hanging sessions...")
    with engine.connect() as conn:
        result = conn.execute(
            sa.text(
                """
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
    logger.info(f"Checking if we need to prune demos...")
    current_size = get_total_storage_usage(minio_client)

    with engine.connect() as conn:
        max_result = conn.execute(
            sa.text(
                """
                SELECT max_storage_gb FROM prune_config;
                """
            )
        )
        max_size_gb = max_result.scalar_one()
        if max_size_gb is None or max_size_gb <= 0:
            logger.warning("No storage limit set, enjoy filling your disk!")
            return False
        max_size = max_size_gb * (1024 ** 3)
        total_bytes_to_remove = current_size - max_size
        logger.info("Current size: %d MB; Max size: %d MB", current_size / (1024 ** 2), max_size / (1024 ** 2))
        if total_bytes_to_remove <= 0:
            logger.info("No need to prune.")
            return False

        logger.info("Attempting to prune %d MB", max(0, total_bytes_to_remove / (1024 ** 2)))

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
            {"session_ids_to_remove": tuple(session_ids_to_remove)}
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
                SELECT max_prune_ratio FROM prune_config;
                """
            )
        )
        # If we're gonna wipe more than max_prune_ratio (default 0.05) of the blobs, something is probably very wrong.
        # Setting this to negative will perform a one-time prune regardless of ratio.
        max_prune_ratio = ratio_result.scalar_one()
        if len(minio_demoblobs_dict) > len(ids_in_db) * max_prune_ratio and max_prune_ratio >= 0:
            logger.warning("Too many orphaned demo blobs: %d (%f%%) found, but limit set to %d (%f%%). Refusing to clean up because something probably broke.",
                len(minio_demoblobs_dict),
                len(minio_demoblobs_dict) / len(ids_in_db) * 100,
                math.floor(len(ids_in_db) * max_prune_ratio),
                max_prune_ratio * 100
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
                {"max_prune_ratio": max_prune_ratio}
            )
            conn.commit()

        for blob in minio_demoblobs_dict.values():
            logger.info("Removing orphaned demo %s", blob.object_name)
            minio_client.remove_object("demoblobs", blob.object_name)
        for blob in minio_jsonblobs_dict.values():
            logger.info("Removing orphaned json %s", blob.object_name)
            minio_client.remove_object("jsonblobs", blob.object_name)

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
