"""Integration tests."""

import asyncio
import time
from typing import Iterator

import pytest
import sqlalchemy as sa
import websockets
from litestar import Litestar
from litestar.status_codes import HTTP_200_OK, HTTP_403_FORBIDDEN
from litestar.testing import TestClient

from masterbase.app import app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def steam_id() -> str:
    """Return steam ID fixture."""
    return '76561111111111111'


@pytest.fixture(scope="module")
def api_key() -> str:
    """Return API key ID fixture."""
    return "my-api-key"


@pytest.fixture(scope="module")
def test_client(steam_id: str, api_key: str) -> Iterator[TestClient[Litestar]]:
    """Yield a test client for testing."""
    with TestClient(app=app) as client:
        with app.state.engine.connect() as conn:
            sql = "INSERT INTO api_keys VALUES (:steam_id, :api_key, NOW(), NOW());"
            conn.execute(sa.text(sql), {"steam_id": steam_id, "api_key": api_key})
            conn.commit()
        yield client


def test_close_session_no_session(test_client: TestClient[Litestar], api_key: str) -> None:
    """Test closing a session yields a 403."""
    response = test_client.get("/close_session", params={"api_key": api_key})
    assert response.status_code == HTTP_403_FORBIDDEN


async def _send_demo_file(test_client: TestClient[Litestar], session_id: str, api_key: str):
    uri = f"ws://{test_client.base_url}/demos?api_key={api_key}&session_id={session_id}"
    async with websockets.connect(uri) as socket:
        time.sleep(5)
        # with open("tests/data/test_demo.dem", "rb") as f:
        with open("song.ogg", "rb") as f:
            while True:
                chunk = f.read(720896)
                time.sleep(0.2)
                if not chunk:
                    break
                await socket.send(chunk)
            await socket.close()


def test_demo_streaming(test_client: TestClient[Litestar], api_key: str) -> None:
    """Test streaming a demo to the DB."""
    response = test_client.get(
        "/session_id",
        params={"api_key": api_key, "fake_ip": "169.254.215.11%3A58480", "map": "asdf", "demo_name": "asdf"},
    )
    session_id = response.json()["session_id"]
    asyncio.run(_send_demo_file(test_client, api_key, session_id))

    response = test_client.get("/close_session", params={"api_key": api_key})
    assert response.status_code == HTTP_200_OK
