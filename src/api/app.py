import base64
import os
from typing import BinaryIO
from uuid import uuid4

from litestar import Litestar, get, websocket, websocket_listener
from litestar.di import Provide


class DemoSessionManager:
    """Helper class to manage incoming data streams."""

    DEMOS_PATH = os.path.expanduser(os.path.join("~/media", "demos"))
    SENTINEL = -1

    def __init__(self) -> None:
        self.file_handles: dict[str, BinaryIO] = {}
        os.makedirs(self.DEMOS_PATH, exist_ok=True)

    def make_or_get_file_handle(self, session_id: str) -> BinaryIO:
        """Take in a session ID and create or return a file handle.

        Args:
            session_id: Session ID.

        Returns:
            File handle for the session_id
        """
        if session_id not in self.file_handles:
            write_path = os.path.join(self.DEMOS_PATH, f"{session_id}.dem")
            self.file_handles[session_id] = open(write_path, "wb")

        return self.file_handles[session_id]

    def handle_demo_data(self, data: dict[str, str | bytes | int]) -> None:
        """Handle incoming data from a client upload.

        Args:
            data: dict of {session_id: ..., data: bytes or self.SENTINEL}
        """
        session_id = str(data["session_id"])

        file_handle = self.make_or_get_file_handle(session_id)

        _data = base64.b64decode(data["data"])

        if _data == self.SENTINEL:
            file_handle.close()
        else:
            file_handle.write(_data)
            file_handle.flush()


demo_manager = DemoSessionManager()


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


@get("/close_session", sync_to_thread=False)
def close_session(session_id: int) -> dict[str, bool]:
    """Close a session out.

    Returns:
        {"closed_successfully": True or False}
    """
    try:
        ...
    except Exception:
        ...

    return {"closed_successfully": ...}


@websocket_listener("/demos")
async def demo_session(data: dict[str, str | bytes | int]) -> dict[str, str]:
    """Handle incoming data from a client upload.

    Smart enough to know where to write data to based on the session ID
    as to handle reconnecting/duplicates.
    """
    demo_manager.handle_demo_data(data)


app = Litestar(route_handlers=[session_id, session_id_active, close_session, demo_session])
