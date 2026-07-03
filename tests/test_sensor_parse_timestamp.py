"""Unit tests for sensor.py's ISO-timestamp parsing helper."""
from __future__ import annotations

from custom_components.fraimic.sensor import _parse_timestamp


def test_parse_timestamp_none_value() -> None:
    assert _parse_timestamp(None) is None


def test_parse_timestamp_non_string_value() -> None:
    assert _parse_timestamp(12345) is None


def test_parse_timestamp_unparseable_string() -> None:
    assert _parse_timestamp("not-a-real-timestamp") is None


def test_parse_timestamp_naive_datetime_assumed_local() -> None:
    result = _parse_timestamp("2026-07-04T07:00:00")
    assert result is not None
    assert result.tzinfo is not None
