"""Tests for _state.py — ChannelState read/write helpers.

PRD-DIST-2400 FR06 prerequisite (Phase B).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trw_mcp.channels._state import (
    ChannelState,
    read_state,
    state_path_for,
    write_state,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(channel_id: str = "test-ch", **kwargs: object) -> ChannelState:
    return ChannelState(channel_id=channel_id, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# state_path_for
# ---------------------------------------------------------------------------


def test_state_path_for_returns_correct_path(tmp_path: Path) -> None:
    result = state_path_for("cc-01", tmp_path)
    assert result == tmp_path / "cc-01-state.json"


def test_state_path_for_uses_channel_id_exactly(tmp_path: Path) -> None:
    result = state_path_for("my_channel-99", tmp_path)
    assert result.name == "my_channel-99-state.json"


# ---------------------------------------------------------------------------
# ChannelState model
# ---------------------------------------------------------------------------


def test_channel_state_defaults() -> None:
    state = ChannelState(channel_id="ch1")
    assert state.schema_version == "channel-state/v1"
    assert state.last_render_sha is None
    assert state.last_render_ts is None
    assert state.last_render_tier is None
    assert state.last_render_tokens_est is None
    assert state.last_render_bytes is None
    assert state.segment_interior_sha256 is None
    assert state.last_sidecar_sha is None
    assert state.ttl_commit_count_at_last_check is None


def test_channel_state_all_fields() -> None:
    state = ChannelState(
        channel_id="cc-01",
        last_render_sha="abc123",
        last_render_ts="2026-05-28T12:00:00.000Z",
        last_render_tier="T2",
        last_render_tokens_est=512,
        last_render_bytes=4096,
        segment_interior_sha256="def456",
        last_sidecar_sha="ghi789",
        ttl_commit_count_at_last_check=5,
    )
    assert state.channel_id == "cc-01"
    assert state.last_render_sha == "abc123"
    assert state.last_render_tier == "T2"
    assert state.last_render_tokens_est == 512


def test_channel_state_schema_version_literal() -> None:
    """schema_version must be exactly 'channel-state/v1'."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ChannelState(channel_id="ch", schema_version="wrong/v0")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# write_state + read_state round-trip
# ---------------------------------------------------------------------------


def test_write_read_round_trip(tmp_path: Path) -> None:
    state = ChannelState(
        channel_id="cc-01",
        last_render_sha="abc",
        last_render_ts="2026-05-28T00:00:00.000Z",
        last_render_tier="T2",
        last_render_tokens_est=100,
        last_render_bytes=800,
        segment_interior_sha256="seg-sha",
        last_sidecar_sha="side-sha",
        ttl_commit_count_at_last_check=3,
    )
    path = tmp_path / "cc-01-state.json"
    write_state(state, path)

    result = read_state(path)
    assert result is not None
    assert result.channel_id == "cc-01"
    assert result.last_render_sha == "abc"
    assert result.last_render_tier == "T2"
    assert result.last_render_tokens_est == 100
    assert result.last_render_bytes == 800
    assert result.segment_interior_sha256 == "seg-sha"
    assert result.ttl_commit_count_at_last_check == 3
    assert result.schema_version == "channel-state/v1"


def test_write_creates_parent_dirs(tmp_path: Path) -> None:
    state = _make_state("ch-nested")
    path = tmp_path / "a" / "b" / "ch-nested-state.json"
    write_state(state, path)
    assert path.exists()


def test_write_atomic_produces_valid_json(tmp_path: Path) -> None:
    state = _make_state("ch-json")
    path = tmp_path / "ch-json-state.json"
    write_state(state, path)
    raw = json.loads(path.read_text())
    assert raw["channel_id"] == "ch-json"
    assert raw["schema_version"] == "channel-state/v1"


# ---------------------------------------------------------------------------
# read_state — missing / corrupt
# ---------------------------------------------------------------------------


def test_read_state_missing_file_returns_none(tmp_path: Path) -> None:
    result = read_state(tmp_path / "nonexistent-state.json")
    assert result is None


def test_read_state_corrupt_json_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "bad-state.json"
    path.write_text("{ not valid json }}}}", encoding="utf-8")
    result = read_state(path)
    assert result is None


def test_read_state_wrong_schema_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "wrong-state.json"
    path.write_text(
        json.dumps({"channel_id": "x", "schema_version": "channel-state/v0"}),
        encoding="utf-8",
    )
    result = read_state(path)
    assert result is None


def test_read_state_never_raises_on_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """read_state must never raise even if read_text raises."""
    path = tmp_path / "perm-state.json"
    path.write_text("{}", encoding="utf-8")

    def _boom(*_a: object, **_kw: object) -> str:
        raise PermissionError("no permission")

    monkeypatch.setattr(Path, "read_text", _boom)
    result = read_state(path)
    assert result is None


# ---------------------------------------------------------------------------
# Atomic write — simulate partial write
# ---------------------------------------------------------------------------


def test_write_atomic_no_partial_file_on_rename_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If os.rename fails, the target should not be corrupted."""
    state = _make_state("ch-atomic")
    path = tmp_path / "ch-atomic-state.json"

    # Write an initial good state
    initial = _make_state("ch-atomic", last_render_sha="original")
    write_state(initial, path)

    # Now make rename fail — simulates crash between write and rename
    original_rename = os.rename

    call_count = {"n": 0}

    def _fail_rename(src: object, dst: object) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("simulated crash")
        original_rename(src, dst)

    monkeypatch.setattr(os, "rename", _fail_rename)

    with pytest.raises(OSError, match="simulated crash"):
        write_state(state, path)

    # Original file must still be intact
    recovered = read_state(path)
    assert recovered is not None
    assert recovered.last_render_sha == "original"
