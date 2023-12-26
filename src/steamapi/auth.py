import json
import os

import toml

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
