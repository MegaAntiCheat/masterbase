"""Global Config."""
import os


def make_db_url() -> str:
    """Correctly make the database URL."""
    user = os.environ["PG_USER"]
    password = os.environ["PG_PASS"]
    return f"postgresql://{user}:{password}@localhost:5432/demos"


demos_db_url = make_db_url()
