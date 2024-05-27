"""Guards for the application."""

from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException, PermissionDeniedException
from litestar.handlers.base import BaseRouteHandler

from masterbase.lib import check_analyst, check_is_active, check_key_exists, session_closed, steam_id_from_api_key
from masterbase.steam import Server, account_exists, get_ip_as_integer, get_steam_api_key


def _development_feature_flag(connection: ASGIConnection) -> bool:
    """Return truthy value of `DEVELOPMENT` feature flag from the app.opts."""
    return connection.app.opt["DEVELOPMENT"]


async def valid_key_guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Guard clause to validate the user's API key."""
    api_key = connection.query_params["api_key"]

    async_engine = connection.app.state.async_engine
    exists = await check_key_exists(async_engine, api_key)
    if not exists:
        raise NotAuthorizedException()


async def analyst_guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Guard clause to User is an analyst."""
    api_key = connection.query_params["api_key"]
    engine = connection.app.state.engine
    steam_id = steam_id_from_api_key(engine, api_key)

    async_engine = connection.app.state.async_engine
    exists = await check_analyst(async_engine, steam_id)
    if not exists:
        raise NotAuthorizedException()


async def user_in_session_guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Assert that the user is not currently in a session."""
    async_engine = connection.app.state.async_engine

    api_key = connection.query_params["api_key"]
    engine = connection.app.state.engine
    steam_id = steam_id_from_api_key(engine, api_key)
    is_active = await check_is_active(async_engine, steam_id)

    if is_active:
        raise PermissionDeniedException(
            detail="User already in a session, either remember your session token or close it out at `/close_session`!"
        )


async def user_not_in_session_guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Assert that the user is not currently in a session."""
    async_engine = connection.app.state.async_engine

    api_key = connection.query_params["api_key"]
    engine = connection.app.state.engine
    steam_id = steam_id_from_api_key(engine, api_key)
    is_active = await check_is_active(async_engine, steam_id)
    if not is_active:
        raise PermissionDeniedException(detail="User is not in a session, create one at `/session_id`!")


async def session_closed_guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Assert that the session is closed."""
    async_engine = connection.app.state.async_engine

    session_id = connection.query_params["session_id"]
    closed = await session_closed(async_engine, session_id)
    if not closed:
        raise PermissionDeniedException(detail="Session is still active, cannot retrieve data!")


async def valid_session_guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Validate session data is from a server we can check exists."""
    if _development_feature_flag(connection):
        return

    fake_ip = connection.query_params["fake_ip"]
    ip, fake_port = fake_ip.split(":")
    converted_fake_ip = get_ip_as_integer(ip)
    api_key = get_steam_api_key()
    try:
        Server.query_from_params(api_key, converted_fake_ip, fake_port)
    except KeyError:
        raise NotAuthorizedException("Cannot accept data from a non-existent gameserver!")


async def valid_target_guard(connection: ASGIConnection, _: BaseRouteHandler):
    """Verify the existence of a target_steam_id, if applicable (otherwise, no-op)."""
    params = connection.query_params
    if "target_steam_id" in params:
        return account_exists(params["target_steam_id"])
    else:
        return True
