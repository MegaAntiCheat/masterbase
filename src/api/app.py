import logging
import os
from datetime import datetime, timezone
from typing import IO, cast
from urllib.parse import urlencode

import requests
import uvicorn
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
    check_steam_id_is_beta_tester,
    generate_uuid4_int,
    is_limited_account,
    provision_api_key,
    update_api_key,
)
from litestar import Litestar, MediaType, Request, WebSocket, get, post
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException, PermissionDeniedException
from litestar.handlers import WebsocketListener
from litestar.handlers.base import BaseRouteHandler
from litestar.response import Redirect
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

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
        raise PermissionDeniedException(
            detail="User already in a session, either remember your session token or close it out at `/close_session`!"
        )


async def user_not_in_session_guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Assert that the user is not currently in a session."""
    async_engine = connection.app.state.async_engine

    api_key = connection.query_params["api_key"]
    is_active = await _check_is_active(async_engine, api_key)
    if not is_active:
        raise PermissionDeniedException(detail="User is not in a session, create one at `/session_id`!")


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

    _session_id = generate_uuid4_int()
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
        os.remove(demo_path)

        # this should never happen but lets be safe
        if latest_session_id in streaming_sessions:
            streaming_sessions.remove(latest_session_id)

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


# use this to ensure client only has one open connection
streaming_sessions: dict[WebSocket, IO] = {}


class DemoHandler(WebsocketListener):
    path = "/demos"
    receive_mode = "binary"

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

        if session_id in streaming_sessions:
            logger.info("User is already streaming!")
            await socket.close()

        path = _make_demo_path(session_id)

        demo_path_exists = os.path.exists(path)
        if demo_path_exists:
            mode = "ab"
            logger.info(f"Found existing handle for session ID: {session_id}")
        else:
            logger.info(f"Creating new handle for session ID: {session_id}")
            mode = "wb"

        streaming_sessions[socket] = open(path, mode)

    def on_disconnect(self, socket: WebSocket) -> None:
        logger.info(f"Received disconnect from session ID: {streaming_sessions[socket].name}")
        streaming_sessions[socket].close()
        streaming_sessions.pop(socket)

    def on_receive(self, data: bytes, socket: WebSocket) -> None:
        logger.info(f"Sinking {len(data)} bytes to to {streaming_sessions[socket].name}")
        streaming_sessions[socket].write(data)


@get("/provision", sync_to_thread=False)
def provision(request: Request) -> Redirect:
    """Provision a login/API key.

    Mostly stolen from https://github.com/TeddiO/pySteamSignIn/blob/master/pysteamsignin/steamsignin.py

    Args:
        request: current request object

    Returns:
        Redirect to the steam sign in
    """

    # enforce https on base_url

    base_url = str(request.base_url)
    if not base_url.startswith("https") and not os.environ["DEVELOPMENT"]:
        base_url = base_url.replace("http", "https")

    auth_params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": f"{base_url}/provision_handler",
        "openid.realm": f"{base_url}/provision_handler",
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
        # block limited accounts...
        limited = is_limited_account(steam_id)
        if limited:
            return

        engine = app.state.engine
        is_beta_tester = check_steam_id_is_beta_tester(engine, steam_id)

        if not is_beta_tester:
            return "<span>You aren't a beta tester! Sorry!</span>"

        api_key = check_steam_id_has_api_key(engine, steam_id)
        new_api_key = generate_uuid4_int()
        invalidated_text = ""
        if api_key is not None:
            # invalidate old API key and provision a new one
            invalidated_text = "Your old key was invalidated!"
            update_api_key(engine, steam_id, new_api_key)

        else:
            provision_api_key(engine, steam_id, new_api_key)

        text = f"Successfully authenticated! Your API key is {new_api_key}! {invalidated_text} Do not lose this as the client needs it!"  # noqa

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
    config = uvicorn.Config("api.app:app", host="0.0.0.0", log_level="info", workers=6, ws_ping_interval=None)
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
