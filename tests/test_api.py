"""Test api utilities."""

import pytest

from src.api.lib import make_db_uri


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
