from __future__ import annotations

import json
import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from senate_audio_audit.errors import InputError
from senate_audio_audit.util import (
    atomic_write_text,
    canonical_json,
    format_timestamp_ms,
    parse_scope,
    parse_timestamp_ms,
    stable_hash,
)


def test_timestamp_round_trip() -> None:
    assert parse_timestamp_ms("01:02:03,004") == 3_723_004
    assert parse_timestamp_ms("1:02:03.04") == 3_723_040
    assert format_timestamp_ms(3_723_004) == "01:02:03.004"


def test_scope_rejects_negative() -> None:
    with pytest.raises(InputError):
        parse_scope("-00:00:01.000")


def test_atomic_write_replaces_content(tmp_path) -> None:
    path = tmp_path / "nested" / "value.txt"
    atomic_write_text(path, "first")
    atomic_write_text(path, "second")
    assert path.read_text(encoding="utf-8") == "second"
    assert list(path.parent.glob(".*.tmp")) == []


def test_canonical_json_rejects_nonfinite() -> None:
    with pytest.raises(ValueError):
        canonical_json({"value": math.nan})


@settings(max_examples=50, deadline=None)
@given(
    st.integers(min_value=0, max_value=999999999),
    st.integers(min_value=1, max_value=1000),
    st.text(alphabet="abcé", min_size=0, max_size=20),
)
def test_stable_hash_depends_on_content(value: int, width: int, text: str) -> None:
    first = stable_hash({"value": value, "width": width, "text": text})
    second = stable_hash({"text": text, "width": width, "value": value})
    assert first == second
    assert json.loads(json.dumps({"hash": first}))["hash"] == first
