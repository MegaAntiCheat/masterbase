import base64
import os
from datetime import datetime, timezone
from typing import BinaryIO, cast
from urllib.parse import urlencode
from uuid import uuid4

import requests
import sqlalchemy as sa
from litestar import Litestar, MediaType, Request, get, websocket, websocket_listener
from litestar.connection import ASGIConnection
from litestar.datastructures import State
from litestar.di import Provide
from litestar.exceptions import NotAuthorizedException
from litestar.handlers import WebsocketListener
from litestar.handlers.base import BaseRouteHandler
from litestar.response import Redirect
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DEMOS_PATH = os.path.expanduser(os.path.join("~/media", "demos"))
os.makedirs(DEMOS_PATH, exist_ok=True)


def _make_db_uri(async_url: bool = False) -> str:
    """Correctly make the database URi."""
    user = os.environ["PG_USER"]
    password = os.environ["PG_PASS"]
    prefix = "postgresql"
    if async_url:
        prefix = f"{prefix}+asyncpg"

    return f"{prefix}://{user}:{password}@localhost:5432/demos"


def get_db_connection(app: Litestar) -> Engine:
    """Returns the db engine.

    If it doesn't exist, creates it and saves it in on the application state object
    """
    if not getattr(app.state, "engine", None):
        app.state.engine = create_engine(_make_db_uri())
    return cast("Engine", app.state.engine)


def close_db_connection(app: Litestar) -> None:
    """Closes the db connection stored in the application State object."""
    if getattr(app.state, "engine", None):
        cast("Engine", app.state.engine).dispose()


def get_async_db_connection(app: Litestar) -> Engine:
    """Returns the async db engine.

    If it doesn't exist, creates it and saves it in on the application state object
    """
    if not getattr(app.state, "async_engine", None):
        app.state.async_engine = create_async_engine(_make_db_uri(async_url=True))
    return cast("AsyncEngine", app.state.async_engine)


async def close_async_db_connection(app: Litestar) -> None:
    """Closes the db connection stored in the application State object."""
    if getattr(app.state, "async_engine", None):
        await cast("AsyncEngine", app.state.async_engine).dispose()


async def valid_key_guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """A Guard clause to validate the user's API key."""
    api_key = connection.query_params["api_key"]

    async_engine = connection.app.state.async_engine

    async with async_engine.connect() as conn:
        result = await conn.execute(sa.text("SELECT * FROM api_keys WHERE api_key = :api_key"), {"api_key": api_key})

        if not result:
            raise NotAuthorizedException()


def create_writer(session_id: str) -> BinaryIO:
    with open(os.path.join(DEMOS_PATH, session_id), "wb") as handle:
        yield handle


def generate_uuid4_int() -> int:
    """Seems useless, but makes testing easier."""
    return uuid4().int


@get("/session_id", guards=[valid_key_guard], sync_to_thread=False)
def session_id(api_key: str) -> dict[str, int]:
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


@get("/session_id_active", guards=[valid_key_guard], sync_to_thread=False)
def session_id_active(api_key: str, session_id: int) -> dict[str, bool]:
    """Tell if a session ID is active by querying the database.

    Returns:
        {"is_active": True or False}
    """
    is_active = ...

    return {"is_active": is_active}


@get("/close_session", guards=[valid_key_guard], sync_to_thread=False)
def close_session(api_key: str, session_id: int) -> dict[str, bool]:
    """Close a session out.

    Returns:
        {"closed_successfully": True or False}
    """
    try:
        ...
    except Exception:
        ...

    return {"closed_successfully": ...}


class DemoHandler(WebsocketListener):
    path = "/demos"
    receive_mode = "binary"

    def on_accept(self, socket: websocket, session_id: str) -> None:
        self.handle = DemoHandler.make_handle(session_id)

    def on_disconnect(self, socket: websocket) -> None:
        self.handle.close()

    def on_receive(self, data: bytes) -> None:
        self.handle.write(data)

    @staticmethod
    def make_handle(session_id: str) -> BinaryIO:
        return open(os.path.join(DEMOS_PATH, f"{session_id}.dem"), "wb")


@get("/provision", sync_to_thread=False)
def provision(request: Request) -> Redirect:
    """Provision a login/API key.

    Mostly stolen from https://github.com/TeddiO/pySteamSignIn/blob/master/pysteamsignin/steamsignin.py

    Args:
        request: current request object

    Returns:
        Redirect to the steam sign in
    """
    auth_params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": f"{request.base_url}/provision_handler",
        "openid.realm": f"{request.base_url}/provision_handler",
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }

    encoded = urlencode(auth_params)

    return Redirect(
        path=f"https://steamcommunity.com/openid/login?{encoded}",
        status_code=303,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


@get("/provision_handler", media_type=MediaType.HTML, sync_to_thread=True)
def provision_handler(request: Request) -> str:
    """Handle a response from Steam.

    Mostly stolen from https://github.com/TeddiO/pySteamSignIn/blob/master/pysteamsignin/steamsignin.py

    Args:
        request: key value request params from the steam sign in to check against.

    Returns:
        Page of HTML for user.
    """
    data = request.query_params
    validation_args = {
        "openid.assoc_handle": data["openid.assoc_handle"],
        "openid.signed": data["openid.signed"],
        "openid.sig": data["openid.sig"],
        "openid.ns": data["openid.ns"],
    }

    signed_args = data["openid.signed"].split(",")
    for item in signed_args:
        arg = f"openid.{item}"
        if data[arg] not in validation_args:
            validation_args[arg] = data[arg]

    validation_args["openid.mode"] = "check_authentication"
    parsed_args = urlencode(validation_args).encode()

    response = requests.get("https://steamcommunity.com/openid/login", params=parsed_args)
    decoded = response.content.decode()
    _, valid_str, _ = decoded.split("\n")
    # valid_str looks like `is_valid:true`
    valid = bool(valid_str.split(":"))

    if not valid:
        text = "Could not log you in!"

    else:
        # great we have the steam id, now lets either provision a new key and display it to the user
        # if it is not in the DB or say that it already exists, and if they forgot it to let an admin know...
        # admin will then delete the steam ID of the user in the DB and a new sign in will work.
        steam_id = os.path.split(data["openid.claimed_id"])[-1]

        with request.app.state.engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT * FROM api_keys WHERE steam_id = :steam_id"), {"steam_id": steam_id}
            ).one_or_none()

            if result is None:
                api_key = uuid4().int
                created_at = datetime.now().astimezone(timezone.utc).isoformat()
                updated_at = created_at
                conn.execute(
                    sa.text(
                        "INSERT INTO api_keys (steam_id, api_key, created_at, updated_at) VALUES (:steam_id, :api_key, :created_at, :updated_at);"  # noqa
                    ),
                    {"steam_id": steam_id, "api_key": api_key, "created_at": created_at, "updated_at": updated_at},
                )
                conn.commit()  # commit changes...
                text = f"You have successfully been authenticated! Your API key is {api_key}! Do not lose this as the client needs it!"  # noqa

            else:
                text = f"Your steam id of {steam_id} already exists in our DB! If you forgot your API key, please let an admin know."  # noqa

    return f"""
        <html>
            <body>
                <div>
                    <span>{text}</span>
                </div>
            </body>
        </html>
        """


app = Litestar(
    on_startup=[get_db_connection, get_async_db_connection],
    route_handlers=[session_id, session_id_active, close_session, DemoHandler, provision, provision_handler],
    on_shutdown=[close_db_connection, close_async_db_connection],
)
