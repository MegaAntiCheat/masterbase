import base64
import time
from importlib.resources import files
from unittest import mock

import pytest
from api.app import app
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


@mock.patch("api.app.generate_uuid4_int")
def test_session_id(mock_uuid4, session_id) -> None:
    with TestClient(app=app) as client:
        mock_uuid4.return_value = session_id
        api_session_id = client.get("/session_id").json()
        assert api_session_id["session_id"] == session_id


@mock.patch("api.app.DemoSessionManager.make_or_get_file_handle")
def test_demo_streaming(mock_handle, demo_file_path, session_id, tmp_path) -> None:
    """Test that a demo is completely received by the API and sunk to a file."""
    write_path = f"{tmp_path}.dem"
    with TestClient(app=app) as client:
        mock_handle.return_value = open(write_path, "wb")
        ws_endpoint = client.websocket_connect("/demos")
        with ws_endpoint as ws:
            with open(demo_file_path, "rb") as f:
                while True:
                    chunk = f.read(DEMO_CHUNKSIZE_BYTES)
                    if not chunk:
                        data = {"session_id": session_id, "data": -1}
                        ws.send_json(data)
                        break

                    encoded_chunk = base64.b64encode(chunk).decode("utf-8")
                    data = {"session_id": session_id, "data": encoded_chunk}
                    ws.send_json(data)

    time.sleep(3)  # wait for buffer to finish sinking
    with open(write_path, "rb") as actual:
        with open(demo_file_path, "rb") as expected:
            assert actual.read() == expected.read()
