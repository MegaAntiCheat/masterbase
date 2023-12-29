from litestar.testing import TestClient

from api.app import app



demo_data = {
    "uploader_steamid": "foo",
    "data": 123,
}

def test_health_check():
    with TestClient(app=app) as client:
        ws_endpoint = client.websocket_connect("/demos")

        with ws_endpoint as ws:
            ws.send_json(demo_data)