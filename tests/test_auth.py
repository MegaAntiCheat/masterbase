import json
import os
from uuid import uuid4

import pytest
import toml
from tf2bots.auth import STEAM_API_KEY_KEYNAME, get_steam_api_key


@pytest.fixture(scope="session")
def steam_id() -> str:
    """Random session-scoped Steam ID."""
    return str(uuid4().int)


def write_json_steam_id(steam_id: str, tmpdir: os.PathLike) -> None:
    """Helper util to write a json JSON Steam ID."""
    data = {STEAM_API_KEY_KEYNAME: steam_id}
    path = os.path.join(tmpdir, "id.json")
    with open(path, "w") as f:
        f.write(json.dumps(data))

    return path


def write_toml_steam_id(steam_id: str, tmpdir: os.PathLike) -> None:
    """Helper util to write a json TOML Steam ID."""
    data = {STEAM_API_KEY_KEYNAME: steam_id}
    path = os.path.join(tmpdir, "id.toml")
    with open(path, "w") as f:
        f.write(toml.dumps(data))

    return path


def write_environment_steam_id(steam_id: str) -> None:
    """Helper util to write an Environment Variable Steam ID."""
    os.environ[STEAM_API_KEY_KEYNAME] = steam_id

    return STEAM_API_KEY_KEYNAME


def test_get_steam_api_key(steam_id: str, tmpdir: os.PathLike) -> None:
    """Test thhe ``get_steam_api_key`` function."""
    # test json
    key_location = write_json_steam_id(steam_id, tmpdir)
    assert get_steam_api_key(key_location) == steam_id

    # test toml
    key_location = write_toml_steam_id(steam_id, tmpdir)
    assert get_steam_api_key(key_location) == steam_id

    # test environment variable
    key_location = write_environment_steam_id(steam_id)
    assert get_steam_api_key(key_location) == steam_id

    with pytest.raises(KeyError):
        key_location = write_environment_steam_id(steam_id)
        os.environ.pop(STEAM_API_KEY_KEYNAME)
        get_steam_api_key(key_location)
