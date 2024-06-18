"""Integration tests."""

import csv
import io
import time
from typing import Iterator

import pytest
import requests
import sqlalchemy as sa
from litestar import Litestar
from litestar.status_codes import HTTP_200_OK, HTTP_201_CREATED, HTTP_403_FORBIDDEN
from litestar.testing import TestClient

from masterbase.app import app
from masterbase.lib import LATE_BYTES_END, LATE_BYTES_START, add_report
from masterbase.models import ReportReason

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
            sql = "TRUNCATE TABLE reports CASCADE;"
            conn.execute(sa.text(sql))
            sql = "TRUNCATE TABLE demo_sessions CASCADE;"
            conn.execute(sa.text(sql))
            sql = "DELETE FROM api_keys WHERE api_key = :api_key;"
            conn.execute(sa.text(sql), {"api_key": api_key})
            sql = "DELETE FROM analyst_steam_ids WHERE steam_id = :steam_id;"
            conn.execute(sa.text(sql), {"steam_id": steam_id})
            conn.commit()


def test_report_reasons_match(test_client: TestClient[Litestar]) -> None:
    """Ensure that Pydantic report reasons match the postgres type registry."""
    with test_client.app.state.engine.connect() as conn:
        cursor = conn.execute(
            sa.text(
                """
            SELECT enumlabel
            FROM pg_enum
            JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
            WHERE pg_type.typname = 'report_reason';
            """
            )
        )
    db_reasons = next(zip(*cursor))
    pd_reasons = tuple(variant.value for variant in ReportReason)
    assert db_reasons == pd_reasons, (
        "Database and Pydantic: ORM mismatch!"
        + f"\n\tDatabase accepts {db_reasons}."
        + f"\n\tPydantic accepts {pd_reasons}."
    )


def test_close_session_no_session(test_client: TestClient[Litestar], api_key: str) -> None:
    """Test closing a session yields a 403."""
    response = test_client.get("/close_session", params={"api_key": api_key})
    assert response.status_code == HTTP_403_FORBIDDEN


def _send_demo_file(test_client: TestClient[Litestar], api_key: str, session_id: str):
    with test_client.websocket_connect("/demos", params={"api_key": api_key, "session_id": session_id}) as socket:
        time.sleep(5)
        with open("tests/data/test_demo.dem", "rb") as f:
            while True:
                chunk = f.read(720896)
                if not chunk:
                    break
                socket.send_bytes(chunk)
                time.sleep(1)
            socket.close()


def test_demo_streaming(test_client: TestClient[Litestar], api_key: str) -> None:
    """Test streaming a demo to the DB."""
    session_id_response = test_client.get(
        "/session_id",
        params={"api_key": api_key, "fake_ip": "169.254.215.11%3A58480", "map": "some_map", "demo_name": "demo.dem"},
    )
    session_id = session_id_response.json()["session_id"]
    _send_demo_file(test_client, api_key, session_id)

    late_bytes_hex = "7031cf44a7af0100cea70100f5e00400"
    late_bytes_response = test_client.post(
        "/late_bytes", params={"api_key": api_key}, json={"late_bytes": late_bytes_hex}
    )  # noqa
    assert late_bytes_response.status_code == HTTP_201_CREATED

    close_session_response = test_client.get("/close_session", params={"api_key": api_key})
    assert close_session_response.status_code == HTTP_200_OK

    response = test_client.get("/demodata", params={"api_key": api_key, "session_id": session_id})
    redirect = str(response.url)
    demo_out = requests.get(redirect).content

    with open("tests/data/test_demo.dem", "rb") as f:
        demo_in = f.read()
        demo_in = demo_in[:LATE_BYTES_START] + bytes.fromhex(late_bytes_hex) + demo_in[LATE_BYTES_END:]
        assert demo_in == demo_out


def test_db_exports(test_client: TestClient[Litestar], api_key: str) -> None:
    """Test on-demand exports from the database."""
    session_id_response = test_client.get(
        "/session_id",
        params={"api_key": api_key, "fake_ip": "169.254.215.11%3A58480", "map": "some_map", "demo_name": "demo.dem"},
    )
    session_id = session_id_response.json()["session_id"]
    # Insert mock reports
    expected = []
    for i in range(10):
        reason = "cheater" if i % 2 == 0 else "bot"
        target_steam_id = f"{i:020d}"
        record = {"session_id": session_id, "target_steam_id": target_steam_id, "reason": reason}
        add_report(test_client.app.state.engine, **record)
        expected.append((session_id, target_steam_id, reason))

    test_client.get("/close_session", params={"api_key": api_key})
    response = test_client.get("/db_export", params={"api_key": api_key, "table": "reports"})
    response_records = csv.DictReader(io.TextIOWrapper(io.BytesIO(response.content)))
    assert set(response_records.fieldnames).issuperset({"session_id", "target_steam_id", "reason"})
    returned = sorted(
        ((record["session_id"], record["target_steam_id"], record["reason"]) for record in response_records),
        key=lambda record: record["created_at"],
    )
    assert tuple(expected) == returned
