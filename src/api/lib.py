import os
from uuid import uuid4


def _make_db_uri(async_url: bool = False) -> str:
    """Correctly make the database URi."""
    user = os.environ["PG_USER"]
    password = os.environ["PG_PASS"]
    prefix = "postgresql"
    if async_url:
        prefix = f"{prefix}+asyncpg"

    return f"{prefix}://{user}:{password}@localhost:5432/demos"


def generate_uuid4_int() -> int:
    """Seems useless, but makes testing easier."""
    return uuid4().int
