"""Integration tests."""

import time
from typing import Iterator

import pytest
import sqlalchemy as sa
from litestar import Litestar
from litestar.response import Stream
from litestar.status_codes import HTTP_200_OK, HTTP_403_FORBIDDEN
from litestar.testing import TestClient

from masterbase.app import app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def steam_id() -> str:
    """Return steam ID fixture."""
    return "76561111111111111"


@pytest.fixture(scope="session")
def api_key() -> str:
    """Return API key ID fixture."""
    return "my-api-key"


@pytest.fixture(scope="session")
def test_client(steam_id: str, api_key: str) -> Iterator[TestClient[Litestar]]:
    """Yield a test client for testing."""
    with TestClient(app=app) as client:
        with app.state.engine.connect() as conn:
            sql = "INSERT INTO api_keys VALUES (:steam_id, :api_key, NOW(), NOW());"
            conn.execute(sa.text(sql), {"steam_id": steam_id, "api_key": api_key})
            sql = "INSERT INTO analyst_steam_ids VALUES (:steam_id);"
            conn.execute(sa.text(sql), {"steam_id": steam_id})
            conn.commit()
        yield client

        with app.state.engine.connect() as conn:
            sql = "DELETE FROM demo_sessions;"
            conn.execute(sa.text(sql))
            sql = "DELETE FROM api_keys WHERE api_key = :api_key;"
            conn.execute(sa.text(sql), {"api_key": api_key})
            sql = "DELETE FROM analyst_steam_ids WHERE steam_id = :steam_id;"
            conn.execute(sa.text(sql), {"steam_id": steam_id})
            conn.commit()


def test_close_session_no_session(test_client: TestClient[Litestar], api_key: str) -> None:
    """Test closing a session yields a 403."""
    response = test_client.get("/close_session", params={"api_key": api_key})
    assert response.status_code == HTTP_403_FORBIDDEN


def _send_demo_file(test_client: TestClient[Litestar], api_key: str, session_id: str):
    with test_client.websocket_connect("/demos", params={"api_key": api_key, "session_id": session_id}) as socket:
        time.sleep(5)
        with open("tests/data/test_demo.dem", "rb") as f:
            while True:
                chunk = f.read(2048)
                if not chunk:
                    break
                socket.send_bytes(chunk)
            socket.close()


def test_demo_streaming(test_client: TestClient[Litestar], api_key: str) -> None:
    """Test streaming a demo to the DB."""
    session_id_response = test_client.get(
        "/session_id",
        params={"api_key": api_key, "fake_ip": "169.254.215.11%3A58480", "map": "some_map", "demo_name": "demo.dem"},
    )
    session_id = session_id_response.json()["session_id"]
    _send_demo_file(test_client, api_key, session_id)

    response = test_client.get("/close_session", params={"api_key": api_key})
    assert response.status_code == HTTP_200_OK

    # this fails :(
    with test_client.stream("GET", "/demodata", params={"api_key": api_key, "session_id": session_id}) as demo_stream:
        demo_out = demo_stream.read()

    with open("tests/data/test_demo.dem", "rb") as f:
        demo_in = f.read()
        assert len(demo_in) == len(demo_out)
