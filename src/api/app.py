from uuid import uuid4

from litestar import Litestar, get, websocket_listener


def generate_uuid4_int() -> int:
    """Seems useless, but makes testing easier."""
    return uuid4().int


@get("/session_id", sync_to_thread=False)
def get_session_id() -> dict[str, int]:
    """Return a session ID, as well as persist to database.

    This is to help us know what is happening downstream:
        - How many active upload sessions
        - If the upload request contains a valid session ID
        - Currently valid upload session ID's so client could reconnect

    """
    session_id = generate_uuid4_int()
    return {"session_id": session_id}


@websocket_listener("/demos")
async def demo_session(data: dict[str, str]) -> dict[str, str]:
    """Handle incoming data from a client upload.

    Smart enough to know where to write data to based on the session ID
    as to handle reconnecting/duplicates. Will eventually interface
    with Minio as the file system abstraction layer.
    """
    print(data)


app = Litestar(route_handlers=[get_session_id, demo_session])
