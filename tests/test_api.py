"""Integration tests."""


from typing import Iterator

import pytest
from litestar import Litestar
from litestar.status_codes import HTTP_403_FORBIDDEN
from litestar.testing import TestClient

from masterbase.app import app

pytestmark = pytest.mark.integration


@pytest.fixture()
def steam_id() -> str:
    """Return steam ID fixture."""
    return 76561111111111111


@pytest.fixture()
def api_key() -> str:
    """Return API key ID fixture."""
    return "my-api-key"


@pytest.fixture(scope="module")
def test_client(steam_id: str, api_key: str) -> Iterator[TestClient[Litestar]]:
    """Yield a test client for testing."""
    with TestClient(app=app) as client:
        with app.state.engine.connect() as conn:
            sql = "INSERT INTO api_keys VALUES (:steam_id, :api_key, NOW(), NOW());"
            conn.execute(sql, {"steam_id": steam_id, "api_key": api_key})
            conn.commit()
        yield client



def test_close_session_no_session(test_client: TestClient[Litestar], api_key: str) -> None:
    """Test closing a session yields a 403."""
    response = test_client.get("/close_session", {"api-key": api_key})
    assert response.status_code == HTTP_403_FORBIDDEN
