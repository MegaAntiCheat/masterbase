import json
import os
import toml

from requests import get
from typing import Any
from pydantic import BaseModel

STEAM_API_KEY_KEYNAME = "STEAM_API_KEY"


def get_steam_api_key(path_or_env_var_name: str) -> str:
    """Get a steam API key from a toml, json, or environment variable.

    Expects key to be "STEAM_API_KEY".

    Args:
        path_or_env_var_name: path to toml, json, or environment variable name.

    Returns:
        steam api key
    """
    try:
        # attempt to load from json or toml file
        if os.path.isfile(path_or_env_var_name):
            with open(path_or_env_var_name, "r") as f:
                if path_or_env_var_name.endswith(".toml"):
                    data = toml.loads(f.read())

                elif path_or_env_var_name.endswith(".json"):
                    data = json.loads(f.read())

                key = data[STEAM_API_KEY_KEYNAME]

        # attempt to load from environment variable
        else:
            key = os.environ[STEAM_API_KEY_KEYNAME]

        return key

    except KeyError:
        raise KeyError(f"Could not find key of {STEAM_API_KEY_KEYNAME} in {path_or_env_var_name}!")


class Filters:
    """Simple method to apply filters to a query.

    See https://developer.valvesoftware.com/wiki/Master_Server_Query_Protocol#Filter

    Yes, I am aware that certain filters are redundant with certain combinations
    but I did not write the api.
    """

    FILTER_PARAMS = (
        "dedicated",
        "secure",
        "gamedir",
        "map",
        "linux",
        "password",
        "empty",
        "full",
        "proxy",
        "appid",
        "napp",
        "noplayers",
        "white",
        "gametype",
        "gamedata",
        "gamedataor",
        "name_match",
        "version_match",
        "collapse_addr_hash",
        "gameaddr",
    )

    def __init__(
        self,
        dedicated: bool | None = None,
        secure: bool | None = None,
        gamedir: str | None = None,
        mapname: str | None = None,  # inconsistency here because polluted namespace with python `map`
        linux: bool | None = None,
        password: bool | None = None,
        empty: bool | None = None,
        full: bool | None = None,
        proxy: bool | None = None,
        appid: int | None = None,
        napp: int | None = None,
        noplayers: bool | None = None,
        white: bool | None = None,
        gametype: list[str] | str | None = None,
        gamedata: list[str] | str | None = None,
        gamedataor: list[str] | str | None = None,
        name_match: str | None = None,
        version_match: str | None = None,
        collapse_addr_hash: bool | None = None,
        gameaddr: str | None = None,
    ) -> None:
        """Filters for the api.steampowered.com/IGameServersService/GetServerList/v1 endpoint.

        Args:
            dedicated: False for non-dedicated, True for dedicated
            secure: False for non-secure, True for secure
            gamedir: can be others but this is a TF2 app...
            mapname: name of the map
            linux: True for linux, False for others
            password: False for no password, True for password protected
            empty: False for empty servers, True for non-empty
            full: False for full servers, True for non-full
            proxy: False for non spectator proxy servers, 1 for spectator proxy
            appid: integer ID of the app https://developer.valvesoftware.com/wiki/Steam_Application_IDs
            napp: integer ID of the app not running https://developer.valvesoftware.com/wiki/Steam_Application_IDs
            noplayers: True for no players, False for players
            white: True for whitelisted, False for non-whitelisted
            gametype: servers with any of the given tag(s) in sv_tags (documented wrong in link)
            gamedata: servers with all of the given tag(s) in their 'hidden' tags (L4D2)
            gamedataor: servers with all of the given tag(s) in their 'hidden' tags (L4D2)
            name_match: servers with their hostname matching [hostname] (can use * as a wildcard)
            version_match: servers running version [version] (can use * as a wildcard)
            collapse_addr_hash: Return only one server for each unique IP
            gameaddr: return only servers on the specified IP address (port supported and optional)
        """
        self.dedicated = Filters.coerce_boolean(dedicated)
        self.secure = Filters.coerce_boolean(secure)
        self.gamedir = gamedir
        self.map = mapname  # inconsistency here because polluted namespace with python `map`
        self.linux = Filters.coerce_boolean(linux)
        self.password = Filters.coerce_boolean(password)
        self.empty = Filters.coerce_boolean(empty)
        self.full = Filters.coerce_boolean(full)
        self.proxy = Filters.coerce_boolean(proxy)
        self.appid = appid
        self.napp = napp
        self.noplayers = Filters.coerce_boolean(noplayers)
        self.white = Filters.coerce_boolean(white)
        self.gametype = Filters.coerce_listable(gametype)
        self.gamedata = Filters.coerce_listable(gamedata)
        self.gamedataor = Filters.coerce_listable(gamedataor)
        self.name_match = name_match
        self.version_match = version_match
        self.collapse_addr_hash = Filters.coerce_boolean(collapse_addr_hash)
        self.gameaddr = gameaddr

        self.nor_filter = []
        self.nand_filter = []

    @staticmethod
    def coerce_boolean(value: bool | None) -> int | None:
        """Coerce a boolean value into an integer or do nothing if it is None.

        Args:
            value: bool or None

        Returns:
            Correctly coerced data.
        """
        if value is None:
            return value

        return int(value)

    @staticmethod
    def coerce_listable(value: list[str] | str | None) -> list[str] | None:
        """Coerce a listable value into a list if it is a string, or do nothing if already a list or None.

        Args:
            value: list[str], str, or None

        Returns:
            Correctly coerced data.
        """
        if value is None or isinstance(value, list):
            return value

        return [value]

    def _make_filter_str(self) -> str:
        r"""Make the filter dict for the query.

        Something like `\appid\440,\gametype\payload,valve`

        Returns:
            formatted filter string for the request
        """
        filters = []
        for filter_param in self.FILTER_PARAMS:
            attr = getattr(self, filter_param)
            if attr is None:
                continue

            if isinstance(attr, list):
                attr = ",".join(attr)

            filters.append(f"\{filter_param}\{attr}")

        # handle defaults
        if not filters:
            return ""

        return "".join(filters)

    @property
    def filter_string(self) -> str:
        return self._make_filter_str()

    def add_nor_filter(self) -> None:
        raise NotImplementedError

    def add_nand_filter(self) -> None:
        raise NotImplementedError


class Server(BaseModel):
    """Represent a server response from https://api.steampowered.com/IGameServersService/GetServerList/v1."""

    addr: str
    gameport: int
    steamid: str
    name: str
    appid: int
    gamedir: str
    version: str
    product: str
    region: int
    players: int
    max_players: int
    bots: int
    map: str
    secure: bool
    dedicated: bool
    os: str
    gametype: str

    URL: str = "https://api.steampowered.com/IGameServersService/QueryByFakeIP/v1/"

    # four query types -- https://developer.valvesoftware.com/wiki/Source_RCON_Protocol
    # 0 is nothing
    # 1 is SDR backed attrs lik above
    # 2 is player data
    # 3 is game rules
    QUERY_TYPES: dict[int, str] = {
        1: "ping_data",
        2: "players_data",
        3: "rules_data",
    }

    @property
    def tags(self) -> list[str]:
        """Property to just make the `gametype` attribute nicer."""
        return self.gametype.split(",")

    @property
    def ip(self) -> str:
        """Property to return IP without port."""
        return self.addr.split(":")[0]

    @property
    def fake_ip(self) -> int:
        """Get the fake IP for a server. Wack math from ChatGPT and @Saghetti."""
        ip_parts = [int(part) for part in self.ip.split(".")]
        return (ip_parts[0] * 256**3) + (ip_parts[1] * 256**2) + (ip_parts[2] * 256) + ip_parts[3]

    def query(self, steam_api_key: str) -> dict[str, Any]:
        """Query for the server information using `QueryByFakeIP` endpoint.

        Note that we use `QueryByFakeIP` because of the steam datagram relay (SDR) protocol.

        Args:
            steam_api_key: steam api key
        """
        server_data = {}

        params = {
            "key": steam_api_key,
            "fake_ip": self.fake_ip,
            "fake_port": self.gameport,
            "app_id": self.appid,
        }
        for query_type, query_key in self.QUERY_TYPES.items():
            params["query_type"] = query_type

            response = get(self.URL, params)
            server_data[query_key] = response.json()["response"][query_key]

        return server_data


class Query:
    """Object that represents a response from the api.steampowered.com/IGameServersService/GetServerList/v1 endpoint."""

    URL = "https://api.steampowered.com/IGameServersService/GetServerList/v1/"

    def __init__(self, steam_api_key: str, filters: dict[str, Any], limit: int | None = None) -> None:
        """Prepare a query for the `GetServerList` endpoint.

        Args:
            steam_api_key: steam api key
            filters: filters, must adhere to the `Filters` class
            limit: limit servers to return. Defaults to None.
        """
        self.steam_api_key = steam_api_key
        self.filters = filters
        self.limit = limit

    def _query(self) -> dict[str, Any]:
        """Helper method to apply filters and query."""
        params = {}

        params["key"] = self.steam_api_key

        filters = Filters(**self.filters)

        params["filter"] = filters.filter_string

        if self.limit is not None:
            params["limit"] = self.limit

        response = get(self.URL, params)

        return response.json()["response"]

    def query(self) -> list[Server]:
        """Thin wrapper for _query() and converts to Pydantic classes."""
        response = self._query()

        if not response:
            raise ValueError("Query returned no servers!")

        servers = [Server(**server) for server in response["servers"]]

        return servers
