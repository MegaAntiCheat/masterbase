"""Integration tests."""

import pytest

pytestmark = pytest.mark.integration


def test_foo():
    assert "foo" == "bar"
