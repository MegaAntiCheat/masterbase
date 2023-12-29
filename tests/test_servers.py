from typing import Any

import pytest
from api.servers import Filters


@pytest.mark.parametrize("value,expected", [(True, 1), (False, 0), (None, None)])
def test_coerce_boolean(value: bool | None, expected: int | None) -> None:
    """Test that boolean params are coerced correctly."""
    actual = Filters.coerce_boolean(value)

    assert actual == expected


@pytest.mark.parametrize("value,expected", [("foo", ["foo"]), (["bar"], ["bar"]), (None, None)])
def test_coerce_listable(value: list[str] | str | None, expected: list[str] | None) -> None:
    """Test that listable params are coerced correctly."""
    actual = Filters.coerce_listable(value)

    assert actual == expected


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({}, ""),
        ({"dedicated": True}, r"filter=\dedicated\1"),
        ({"dedicated": True, "secure": False, "mapname": "asdf"}, r"filter=\dedicated\1,\secure\0,\map\asdf"),
        ({"dedicated": True, "gametype": ["foo", "bar"]}, r"filter=\dedicated\1,\gametype\foo,bar"),
        (
            {"dedicated": True, "secure": True, "gametype": ["foo", "bar"]},
            r"filter=\dedicated\1,\secure\1,\gametype\foo,bar",
        ),
    ],
)
def test_filters(kwargs: dict[str, Any], expected: str) -> None:
    filters = Filters(**kwargs)

    actual = filters._make_filter_str()
    assert actual == expected
