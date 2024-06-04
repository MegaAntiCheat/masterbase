"""Integration tests."""

import pytest
from masterbase.lib import make_db_uri
from litestar.testing import TestClient
from masterbase.app import app

pytestmark = pytest.mark.integration


def test_health_check():
    with TestClient(app=app) as client:
        print(app.state.engine)
