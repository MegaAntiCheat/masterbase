from unittest import mock

from api.app import app
from litestar.testing import TestClient


@mock.patch("api.app.generate_uuid4_int")
def test_session_id(mock_uuid4) -> None:
    with TestClient(app=app) as client:
        mock_uuid4.return_value = 123
        session_id = client.get("/session_id").json()
        assert session_id["session_id"] == 123


demo_data = {
    "uploader_steamid": "foo",
    "data": 123,
}


def test_health_check() -> None:
    with TestClient(app=app) as client:
        ws_endpoint = client.websocket_connect("/demos")

        with ws_endpoint as ws:
            ws.send_json(demo_data)
