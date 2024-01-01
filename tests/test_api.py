import base64
import time
from importlib.resources import files
from unittest import mock

import pytest
from api.app import app
from litestar.exceptions import NotAuthorizedException
from litestar.testing import TestClient

from tests import data

DEMO_CHUNKSIZE_BYTES = 1024


@pytest.fixture
def demo_file_path() -> str:
    source = files(data).joinpath("test_demo.dem")
    return str(source)


@pytest.fixture
def session_id() -> int:
    return 123


@pytest.fixture
def client() -> TestClient:
    yield TestClient(app=app)


def test_guard(client, session_id) -> None:
    with client as test_client:
        response = test_client.get("/session_id", params={"api_key": "foo"}).json()
        assert response == {"status_code": 401, "detail": "Unauthorized"}


@mock.patch("api.app.DemoHandler.make_handle")
def test_demo_streaming(handle_func, client, demo_file_path, session_id, tmp_path) -> None:
    """Test that a demo is completely received by the API and sunk to a file."""
    write_path = f"{tmp_path}.dem"
    handle_func.return_value = open(write_path, "wb")
    with client as test_client:
        with test_client.websocket_connect("/demos", params={"session_id": session_id}) as socket:
            with open(demo_file_path, "rb") as f:
                while True:
                    chunk = f.read(DEMO_CHUNKSIZE_BYTES)
                    if not chunk:
                        socket.close()
                        break
                    socket.send_bytes(chunk)

    time.sleep(3)  # wait for buffer to finish sinking
    with open(write_path, "rb") as actual:
        with open(demo_file_path, "rb") as expected:
            assert actual.read() == expected.read()
