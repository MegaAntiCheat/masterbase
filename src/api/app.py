from uuid import uuid4

from litestar import Litestar, get, websocket_listener


def generate_uuid4_int() -> int:
    """Seems useless, but makes testing easier."""
    return uuid4().int


@get("/session_id", sync_to_thread=False)
def session_id() -> dict[str, int]:
    """Return a session ID, as well as persist to database.

    This is to help us know what is happening downstream:
        - How many active upload sessions
        - If the upload request contains a valid session ID
        - Currently valid upload session ID's so client could reconnect

    Returns:
        {"session_id": some integer}
    """
    session_id = generate_uuid4_int()
    return {"session_id": session_id}


@get("/session_id_active", sync_to_thread=False)
def session_id_active(session_id: int) -> dict[str, bool]:
    """Tell if a session ID is active by querying the database.

    Returns:
        {"is_active": True or False}
    """
    is_active = ...

    return {"is_active": is_active}


def close_session(session_id: int) -> dict[str, bool]:
    """Close a session out.

    Returns:
        {"closed_successfully": True or False}
    """
    try:
        ...
    except Exception as e:
        ...

    return {"closed_successfully": ...}


@websocket_listener("/demos")
async def demo_session(data: dict[str, str]) -> dict[str, str]:
    """Handle incoming data from a client upload.

    Smart enough to know where to write data to based on the session ID
    as to handle reconnecting/duplicates. Will eventually interface
    with Minio as the file system abstraction layer.
    """
    print(data)


app = Litestar(route_handlers=[session_id, session_id_active, close_session, demo_session])
