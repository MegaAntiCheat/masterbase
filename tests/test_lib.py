"""Test api utilities."""

import io
import os
import random

import numpy as np
import pytest

from masterbase.anomaly import DetectionState
from masterbase.lib import DEMOS_PATH, ConcatStream, DemoSessionManager, generate_uuid4_int, make_db_uri


@pytest.fixture(scope="session")
def session_id() -> str:
    """Session ID fixture."""
    return str(generate_uuid4_int())


@pytest.fixture
def mock_os_environ(monkeypatch):
    """Mock environment."""
    monkeypatch.setenv("POSTGRES_USER", "test_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test_password")
    monkeypatch.setenv("POSTGRES_HOST", "test_host")
    monkeypatch.setenv("POSTGRES_PORT", "8050")


@pytest.mark.parametrize(
    "is_async,expected_uri",
    [
        (True, "postgresql+asyncpg://test_user:test_password@test_host:8050/demos"),
        (False, "postgresql://test_user:test_password@test_host:8050/demos"),
    ],
)
def test_make_db_uri(mock_os_environ, is_async: bool, expected_uri: str) -> None:
    """Test `make_db_uri`."""
    actual = make_db_uri(is_async)
    assert actual == expected_uri


def test_make_demo_path(session_id: str) -> None:
    """Test make demo path."""
    manager = DemoSessionManager(session_id=session_id, detection_state=DetectionState())
    assert manager.demo_path == os.path.join(DEMOS_PATH, f"{session_id}.dem")


def test_concat_stream_bounds() -> None:
    """Test ConcatStream, in particular its handling of random stream boundaries."""
    # concatenate ten "streams"
    data = random.randbytes(1 << 20)

    # make up some random read intervals
    stream_splits = sorted(random.choices(range(len(data)), k=127))
    stream_slices = (
        (slice(stream_splits[0]),)
        + tuple((slice(start, end) for start, end in zip(stream_splits[:-1], stream_splits[1:])))
        + (slice(stream_splits[-1], None),)
    )

    strm = ConcatStream(*(io.BytesIO(data[s]) for s in stream_slices))
    read_splits = random.choices(range(len(data)), k=127)
    read_lengths = np.diff([0] + sorted(read_splits)).tolist()
    read = bytearray()
    for n in read_lengths:
        chunk = strm.read(n)
        assert len(chunk) == n
        read += chunk
    read += strm.read()
    assert read == data
