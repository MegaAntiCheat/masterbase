import os
import logging
import requests
from datetime import datetime, timezone
from typing import cast, BinaryIO
from urllib.parse import urlencode

from multidict import MultiDict
from uvicorn import Config, Server

from litestar import Litestar, MediaType, Request, WebSocket, get, post
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.handlers import WebsocketListener
from litestar.handlers.base import BaseRouteHandler
from litestar.response import Redirect
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from api.lib import (
    _check_is_active,
    _check_key_exists,
    _close_session,
    _close_session_with_demo,
    _get_latest_session_id,
    _late_bytes,
    _make_db_uri,
    _make_demo_path,
    _start_session,
    check_steam_id_has_api_key,
    generate_uuid4_int,
    provision_api_key,
)

logger = logging.getLogger(__name__)


def get_db_connection(app: Litestar) -> Engine:
    """Returns the db engine.

    If it doesn't exist, creates it and saves it in on the application state object
    """
    if not getattr(app.state, "engine", None):
        app.state.engine = create_engine(_make_db_uri(), pool_pre_ping=True)
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
        app.state.async_engine = create_async_engine(_make_db_uri(async_url=True), pool_pre_ping=True)
    return cast("AsyncEngine", app.state.async_engine)


async def close_async_db_connection(app: Litestar) -> None:
    """Closes the db connection stored in the application State object."""
    if getattr(app.state, "async_engine", None):
        await cast("AsyncEngine", app.state.async_engine).dispose()


async def valid_key_guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """A Guard clause to validate the user's API key."""
    api_key = connection.query_params["api_key"]

    async_engine = connection.app.state.async_engine
    exists = await _check_key_exists(async_engine, api_key)
    if not exists:
        raise NotAuthorizedException()


async def user_in_session_guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Assert that the user is not currently in a session."""
    async_engine = connection.app.state.async_engine

    api_key = connection.query_params["api_key"]
    is_active = await _check_is_active(async_engine, api_key)

    if is_active:
        raise NotAuthorizedException(
            detail="User already in a session, either remember your session token or close it out at `/close_session`!"
        )


async def user_not_in_session_guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Assert that the user is not currently in a session."""
    async_engine = connection.app.state.async_engine

    api_key = connection.query_params["api_key"]
    is_active = await _check_is_active(async_engine, api_key)
    if not is_active:
        raise NotAuthorizedException(detail="User is not in a session, create one at `/session_id`!")


@get("/session_id", guards=[valid_key_guard, user_in_session_guard], sync_to_thread=False)
def session_id(
    request: Request,
    api_key: str,
    demo_name: str,
    fake_ip: str,
    map: str,
) -> dict[str, str]:
    """Return a session ID, as well as persist to database.

    This is to help us know what is happening downstream:
        - How many active upload sessions
        - If the upload request contains a valid session ID
        - Currently valid upload session ID's so client could reconnect

    Returns:
        {"session_id": some integer}
    """

    _session_id = str(generate_uuid4_int())
    engine = request.app.state.engine
    _start_session(engine, api_key, _session_id, demo_name, fake_ip, map)

    return {"session_id": _session_id}


@get("/close_session", guards=[valid_key_guard, user_not_in_session_guard], sync_to_thread=False)
def close_session(request: Request, api_key: str) -> dict[str, bool]:
    """Close a session out.

    Returns:
        {"closed_successfully": True}
    """
    engine = request.app.state.engine

    latest_session_id = _get_latest_session_id(engine, api_key)
    demo_path = _make_demo_path(latest_session_id)
    demo_path_exists = os.path.exists(demo_path)

    current_time = datetime.now().astimezone(timezone.utc)

    if latest_session_id is None or not demo_path_exists:
        _close_session(engine, api_key, current_time)

    elif latest_session_id is not None and demo_path_exists:
        _close_session_with_demo(engine, api_key, latest_session_id, current_time, demo_path)

    else:
        logger.error(f"Found orphaned session and demo at {demo_path}")

    return {"closed_successfully": True}


@post("/late_bytes", guards=[valid_key_guard, user_not_in_session_guard], sync_to_thread=False)
def late_bytes(request: Request, api_key: str, data: dict[str, str]) -> dict[str, bool]:
    """Add late bytes to a closed demo session..

    Returns:
        {"late_bytes": True}
    """
    engine = request.app.state.engine
    current_time = datetime.now().astimezone(timezone.utc)
    late_bytes = bytes.fromhex(data["late_bytes"])
    _late_bytes(engine, api_key, late_bytes, current_time)

    return {"late_bytes": True}


class DemoHandler(WebsocketListener):
    path = "/demos"
    receive_mode = "binary"
    # populated via 'on_accept'
    api_key: str = None
    session_id: str = None
    handle: BinaryIO = None

    async def on_accept(self, socket: WebSocket, api_key: str, session_id: str) -> None:
        engine = socket.app.state.async_engine
        exists = await _check_key_exists(engine, api_key)
        if not exists:
            logger.info("Invalid API key, closing!")
            await socket.close()

        active = await _check_is_active(engine, api_key)
        if not active:
            logger.info("User is not in a session, closing!")
            await socket.close()

        self.api_key = api_key
        self.session_id = session_id
        self.path = _make_demo_path(self.session_id)
        self.handle = open(self.path, "wb")

    def on_disconnect(self, socket: WebSocket) -> None:
        logger.info("Received disconnect!")
        self.handle.close()

        engine = socket.app.state.engine
        current_time = datetime.now().astimezone(timezone.utc)

        _close_session_with_demo(engine, self.api_key, self.session_id, current_time, self.path)

        os.remove(self.path)

    def on_receive(self, data: bytes) -> None:
        self.handle.write(data)


def process_openid_response(request: Request) -> tuple[MultiDict, str]:
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
    return data, response.content.decode('utf8')


@get("/bad_login", sync_to_thread=False)
def login_failed(request: Request) -> str:
    text = "Could not log you in!"
    return f"""
            <html>
                <body>
                    <div>
                        <span>{text}</span>
                    </div>
                </body>
            </html>
            """


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


@get("/login", sync_to_thread=False)
def login(request: Request) -> Redirect:
    auth_params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": f"{request.base_url}/profile",
        "openid.realm": f"{request.base_url}/profile",
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }

    encoded = urlencode(auth_params)

    return Redirect(
        path=f"https://steamcommunity.com/openid/login?{encoded}",
        status_code=303,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


@get("/profile", media_type=MediaType.HTML, sync_to_thread=True)
def profile(request: Request) -> str | Redirect:
    """
    Use Steam OpenID response to authenticate access, then return stored DB detailing that players profile.

    Args:
        request: key value request params from the steam sign in to check against.

    Returns:
        Page of HTML for user.
    """
    data, decoded = process_openid_response(request)

    _, valid_str, _ = decoded.split("\n")
    # valid_str looks like `is_valid:true`
    valid = bool(valid_str.split(":"))

    if not valid:
        return Redirect(
            path=f"/login_failed",
            status_code=303,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    # great we have the steam id, now let's either provision a new key and display it to the user
    # if it is not in the DB or say that it already exists, and if they forgot it to let an admin know...
    # admin will then delete the steam ID of the user in the DB and a new sign in will work.
    steam_id = os.path.split(data["openid.claimed_id"])[-1]
    engine = app.state.engine
    has_key = check_steam_id_has_api_key(engine, steam_id)

    if has_key:


    else:
        text = f"You do not have an API key! <a href='/provision'>Provision one first.</a>"


@get("/provision_handler", media_type=MediaType.HTML, sync_to_thread=True)
def provision_handler(request: Request) -> str | Redirect:
    """Handle a response from Steam.

    Mostly stolen from https://github.com/TeddiO/pySteamSignIn/blob/master/pysteamsignin/steamsignin.py

    Args:
        request: key value request params from the steam sign in to check against.

    Returns:
        Page of HTML for user.
    """
    data, decoded = process_openid_response(request)

    _, valid_str, _ = decoded.split("\n")
    # valid_str looks like `is_valid:true`
    valid = bool(valid_str.split(":"))

    if not valid:
        return Redirect(
            path=f"/login_failed",
            status_code=303,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    # great we have the steam id, now let's either provision a new key and display it to the user
    # if it is not in the DB or say that it already exists, and if they forgot it to let an admin know...
    # admin will then delete the steam ID of the user in the DB and a new sign in will work.
    steam_id = os.path.split(data["openid.claimed_id"])[-1]
    engine = app.state.engine
    has_key = check_steam_id_has_api_key(engine, steam_id)

    if not has_key:
        api_key = str(generate_uuid4_int())
        provision_api_key(engine, steam_id, api_key)
        text = f"Successfully authenticated! Your API key is {api_key}! Do not lose this as the client needs it!"

    else:
        text = f"Steam ID {steam_id} already exists! If you forgot your API key, please let an admin know."

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
    route_handlers=[session_id, close_session, DemoHandler, provision, provision_handler, late_bytes],
    on_shutdown=[close_db_connection, close_async_db_connection],
)


def main() -> None:
    config = Config("api.app:app", host="0.0.0.0", log_level="debug", workers=os.cpu_count(), ws_ping_interval=None)
    server = Server(config)
    server.run()


if __name__ == "__main__":
    main()
