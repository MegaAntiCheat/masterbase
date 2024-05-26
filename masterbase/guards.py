"""Guards for the application."""
import logging
from typing import Optional, Any
from ipaddress import ip_address, IPv4Address

from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException, PermissionDeniedException
from litestar.handlers.base import BaseRouteHandler
from sourceserver.sourceserver import SourceError, SourceServer

from masterbase.lib import check_analyst, check_is_active, check_key_exists, session_closed, steam_id_from_api_key
from masterbase.steam import Server, get_steam_api_key, a2s_server_query


logger = logging.getLogger(__name__)


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


def _perform_server_queries(
        proposed_ip: IPv4Address,
        proposed_port: int,
        api_key: str,
        *,
        invert_selection: bool = False
) -> Optional[SourceServer | dict[str, Any] | Exception]:
    _result = None
    _exception = None
    try:
        # Optional: We could check for additional IP address properties here such as `is_global` or `is_private`
        # All SDR-enabled servers report a link-local multicast IP address as the server IP.
        if proposed_ip.is_link_local and not invert_selection:
            logging.info(f"Querying for gameserver on {proposed_ip} by FakeIP API endpoint.")
            _result = Server.query_from_params(api_key, proposed_ip, proposed_port)
        else:
            logging.info(f"Querying gameserver on {proposed_ip} directly using A2S.")
            _result = a2s_server_query(proposed_ip, proposed_port)
    except (KeyError, SourceError):
        _exception = NotAuthorizedException("Cannot accept data from a non-existent gameserver!")
        logger.info("No appropriate response from game server")
    finally:
        # One last attempt at grabbing a result by performing the opposite query to see if the server talks
        if _result is None and not invert_selection:
            logger.info("(Attempt 2/2) Trying alternate query method")
            temp = _perform_server_queries(proposed_ip, proposed_port, api_key, invert_selection=True)
            if isinstance(temp, (Exception, SourceError)):
                _exception = temp
            elif isinstance(temp, (SourceServer, dict)):
                _result = temp

    return _result if _result is not None else _exception


async def valid_session_guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Validate session data is from a server we can check exists."""
    if _development_feature_flag(connection):
        return

    fake_ip = connection.query_params["fake_ip"]
    ip, fake_port = fake_ip.split(":")
    converted_fake_ip: IPv4Address = ip_address(ip)
    api_key = get_steam_api_key()

    _resp = _perform_server_queries(converted_fake_ip, fake_port, api_key)
    if isinstance(_resp, (Exception, SourceError)):
        raise _resp
    else:
        logger.info(f"Confirmed valid session.")
