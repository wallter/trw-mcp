"""Tests for _telemetry.py — channel-events JSONL appender.

PRD-DIST-2400 FR08, FR09, FR10, FR11.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trw_mcp.channels._telemetry import (
    CHANNEL_EVENT_SCHEMA_VERSION,
    CHANNEL_EVENT_V1_REQUIRED,
    MAX_EVENTS_BYTES,
    MAX_EVENTS_LINES,
    PRUNE_LINES_ON_CAP,
    RECORD_ID_PATH_KEYED_RE,
    RECORD_ID_SLUG_KEYED_RE,
    VALID_EVENT_TYPES,
    append_channel_event,
    prune_channel_events,
    validate_event_type,
    validate_record_id,
)

# ---------------------------------------------------------------------------
# Constants (FR10)
# ---------------------------------------------------------------------------


def test_schema_version_constant() -> None:
    assert CHANNEL_EVENT_SCHEMA_VERSION == "channel-event/v1"


def test_required_fields_tuple() -> None:
    assert CHANNEL_EVENT_V1_REQUIRED == (
        "schema_version",
        "channel_id",
        "client",
        "ts",
        "event_type",
    )


def test_valid_event_types_count() -> None:
    # 20 canonical channel events from master plan §6.2
    # + 2 renderer-distinct events (channel_lock_skip, channel_error — HIGH-1 fix)
    # + 1 system recovery event (manifest_recovered, FR15 / SYS-04)
    assert len(VALID_EVENT_TYPES) == 23


def test_valid_event_types_contains_canonical() -> None:
    # The 20 channel-level events from master plan §6.2
    channel_events = {
        "push_write",
        "push_ephemeral",
        "pull_tool_call",
        "push_stale",
        "quota_exceeded",
        "tier_down",
        "channel_conflict",
        "snapshot_written",
        "snapshot_stale",
        "explorer_invoked",
        "explorer_completed",
        "edit_correlated",
        "hook_installed",
        "channel_disabled",
        "memory_index_near_cap",
        "mdc_tombstone",
        "mdc_conflict_skip",
        "subagent_outcome",
        "throttle_applied",
        "throttle_cleared",
    }
    assert channel_events.issubset(VALID_EVENT_TYPES)


def test_valid_event_types_contains_distinct_renderer_events() -> None:
    # HIGH-1 fix: lock-skip and internal error get distinct event types
    assert "channel_lock_skip" in VALID_EVENT_TYPES
    assert "channel_error" in VALID_EVENT_TYPES


def test_valid_event_types_contains_system_recovery_event() -> None:
    # manifest_recovered is emitted on SYS-04 manifest auto-recovery (FR15)
    assert "manifest_recovered" in VALID_EVENT_TYPES


def test_max_events_bytes() -> None:
    assert MAX_EVENTS_BYTES == 10 * 1024 * 1024


def test_max_events_lines() -> None:
    assert MAX_EVENTS_LINES == 50_000


def test_prune_lines_on_cap() -> None:
    assert PRUNE_LINES_ON_CAP == 25_000


# ---------------------------------------------------------------------------
# validate_record_id (FR11)
# ---------------------------------------------------------------------------


def test_validate_record_id_path_keyed_valid() -> None:
    assert validate_record_id("hotspot:backend/routers/admin.py@a1b2c3d4") is True


def test_validate_record_id_path_keyed_longer_sha() -> None:
    assert validate_record_id("edge_case:src/utils/helpers.py@abcdef0123456789") is True


def test_validate_record_id_slug_keyed_valid() -> None:
    assert validate_record_id("convention:yaml-safe") is True
    assert validate_record_id("edge_case:42") is True
    assert validate_record_id("decision:retry-logic") is True


def test_validate_record_id_invalid_risk_score_format() -> None:
    with pytest.raises(ValueError, match="record_id"):
        validate_record_id("risk-score:backend/routers/admin.py@a1b2")


def test_validate_record_id_invalid_hotspot_state_paths() -> None:
    with pytest.raises(ValueError):
        validate_record_id("hotspot-state_paths")


def test_validate_record_id_invalid_colon_version() -> None:
    with pytest.raises(ValueError):
        validate_record_id("hotspot:backend/routers/admin.py:v3")


def test_validate_record_id_invalid_empty() -> None:
    with pytest.raises(ValueError):
        validate_record_id("")


def test_validate_record_id_invalid_no_colon() -> None:
    with pytest.raises(ValueError):
        validate_record_id("hotspot_nocolon")


def test_validate_record_id_path_keyed_regex_directly() -> None:
    assert RECORD_ID_PATH_KEYED_RE.match("hotspot:a/b/c.py@abcd1234") is not None
    assert RECORD_ID_PATH_KEYED_RE.match("risk-score:x@abc") is None  # hyphen in type


def test_validate_record_id_slug_keyed_regex_directly() -> None:
    assert RECORD_ID_SLUG_KEYED_RE.match("convention:yaml-safe") is not None
    assert RECORD_ID_SLUG_KEYED_RE.match("edge_case:42") is not None


# ---------------------------------------------------------------------------
# append_channel_event — writes valid JSONL (FR08, NFR10)
# ---------------------------------------------------------------------------


def test_append_channel_event_writes_valid_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "channel-events.jsonl"
    append_channel_event(
        channel_id="cc-01",
        client="claude-code",
        event_type="push_write",
        log_path=log_path,
    )
    assert log_path.exists()
    line = log_path.read_text(encoding="utf-8").strip()
    assert line  # non-empty
    event = json.loads(line)
    assert event["schema_version"] == "channel-event/v1"
    assert event["channel_id"] == "cc-01"
    assert event["client"] == "claude-code"
    assert event["event_type"] == "push_write"
    assert "ts" in event
    # ts must end with Z
    assert event["ts"].endswith("Z")


def test_append_channel_event_all_required_fields(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    append_channel_event(
        channel_id="ch",
        client="codex",
        event_type="tier_down",
        log_path=log_path,
    )
    event = json.loads(log_path.read_text().strip())
    for field in CHANNEL_EVENT_V1_REQUIRED:
        assert field in event, f"Required field {field!r} missing"


def test_append_channel_event_optional_fields_included(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    append_channel_event(
        channel_id="ch",
        client="claude-code",
        event_type="push_write",
        log_path=log_path,
        tier="T2",
        tokens_emitted=215,
        sha="a1b2c3d4",
        bytes_emitted=1024,
    )
    event = json.loads(log_path.read_text().strip())
    assert event["tier"] == "T2"
    assert event["tokens_emitted"] == 215
    assert event["sha"] == "a1b2c3d4"
    assert event["bytes_emitted"] == 1024


def test_append_channel_event_none_optional_fields_excluded(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    append_channel_event(
        channel_id="ch",
        client="claude-code",
        event_type="push_write",
        log_path=log_path,
        tier=None,
        tokens_emitted=None,
    )
    event = json.loads(log_path.read_text().strip())
    assert "tier" not in event
    assert "tokens_emitted" not in event


def test_append_multiple_events_all_valid_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    for i in range(100):
        append_channel_event(
            channel_id=f"ch-{i % 5}",
            client="codex",
            event_type="push_write",
            log_path=log_path,
            iteration=i,
        )
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 100
    for line in lines:
        assert json.loads(line)["schema_version"] == "channel-event/v1"


def test_append_channel_event_creates_parent_dirs(tmp_path: Path) -> None:
    log_path = tmp_path / "deep" / "nested" / "events.jsonl"
    append_channel_event(
        channel_id="ch",
        client="claude-code",
        event_type="push_write",
        log_path=log_path,
    )
    assert log_path.exists()


# ---------------------------------------------------------------------------
# append_channel_event — fail-open (FR08, NFR06)
# ---------------------------------------------------------------------------


def test_append_channel_event_fail_open_on_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "events.jsonl"

    def _boom(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", _boom)
    # Must not raise
    append_channel_event(
        channel_id="ch",
        client="claude-code",
        event_type="push_write",
        log_path=log_path,
    )
    assert not log_path.exists()


def test_append_channel_event_fail_open_on_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "events.jsonl"

    def _boom(*_a: object, **_kw: object) -> None:
        raise PermissionError("not allowed")

    monkeypatch.setattr("builtins.open", _boom)
    # Must not raise
    append_channel_event(
        channel_id="ch",
        client="claude-code",
        event_type="push_write",
        log_path=log_path,
    )
    assert not log_path.exists()


def test_append_channel_event_fail_open_on_json_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if json.dumps raises, the function must not propagate."""
    log_path = tmp_path / "events.jsonl"

    import trw_mcp.channels._telemetry as tel_mod

    original_dumps = json.dumps
    call_count = {"n": 0}

    def _bad_dumps(obj: object, **kwargs: object) -> str:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise TypeError("non-serializable")
        return original_dumps(obj, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(tel_mod.json, "dumps", _bad_dumps)
    # Must not raise
    append_channel_event(
        channel_id="ch",
        client="claude-code",
        event_type="push_write",
        log_path=log_path,
    )
    assert call_count["n"] == 1
    assert not log_path.exists()


# ---------------------------------------------------------------------------
# Unknown event_type raises ValueError (FR10)
# ---------------------------------------------------------------------------


def test_unknown_event_type_is_fail_open(tmp_path: Path) -> None:
    """HIGH-6: NFR06 fail-open wins over FR10 raises-at-call-site.

    Spec tension: FR10 says unknown event_type "raises ValueError at call site";
    NFR06 says telemetry must never break a tool call.  Resolution:
    - append_channel_event() remains fail-open — unknown types are silently
      dropped (logged at WARNING, never propagated to callers).
    - validate_event_type() is a public helper for authoring-time checks.
    This test documents that the fail-open behavior is INTENTIONAL (NFR06 wins).
    """
    log_path = tmp_path / "events.jsonl"
    # append_channel_event wraps all exceptions including unknown event_type;
    # must not raise even when event_type is invalid.
    append_channel_event(
        channel_id="ch",
        client="claude-code",
        event_type="not_a_real_event",
        log_path=log_path,
    )
    # Unknown event type → event is dropped (not written to JSONL)
    # File should not exist or should be empty if no other events were appended
    if log_path.exists():
        lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        unknown_lines = [l for l in lines if "not_a_real_event" in l]
        assert not unknown_lines, "Unknown event_type must NOT be written to the JSONL file"


def test_validate_event_type_accepts_canonical_type() -> None:
    """HIGH-6: validate_event_type() is the authoring-time check helper."""
    # Valid canonical type — must not raise
    validate_event_type("push_write")
    validate_event_type("manifest_recovered")
    validate_event_type("channel_lock_skip")
    validate_event_type("channel_error")
    assert {"push_write", "manifest_recovered", "channel_lock_skip", "channel_error"} <= VALID_EVENT_TYPES


def test_validate_event_type_raises_for_unknown_type() -> None:
    """HIGH-6: validate_event_type() raises ValueError for unknown types."""
    with pytest.raises(ValueError, match="not in VALID_EVENT_TYPES"):
        validate_event_type("not_a_real_event_type")


def test_event_v1_schema_required_fields_constant() -> None:
    """The CHANNEL_EVENT_V1_REQUIRED constant must enumerate the 5 required fields."""
    assert set(CHANNEL_EVENT_V1_REQUIRED) == {"schema_version", "channel_id", "client", "ts", "event_type"}


# ---------------------------------------------------------------------------
# record_id format validation in events (FR11)
# ---------------------------------------------------------------------------


def test_record_id_format_valid_path_keyed(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    append_channel_event(
        channel_id="ch",
        client="claude-code",
        event_type="push_write",
        log_path=log_path,
        record_ids=["hotspot:backend/routers/admin.py@a1b2c3d4"],
    )
    event = json.loads(log_path.read_text().strip())
    assert event["record_ids"] == ["hotspot:backend/routers/admin.py@a1b2c3d4"]


def test_record_id_format_valid_slug_keyed(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    append_channel_event(
        channel_id="ch",
        client="claude-code",
        event_type="push_write",
        log_path=log_path,
        record_ids=["convention:yaml-safe", "edge_case:42"],
    )
    event = json.loads(log_path.read_text().strip())
    assert event["record_ids"] == ["convention:yaml-safe", "edge_case:42"]


def test_record_id_invalid_format_does_not_drop_event(tmp_path: Path) -> None:
    """Invalid record_id logs warning but event is still written."""
    log_path = tmp_path / "events.jsonl"
    append_channel_event(
        channel_id="ch",
        client="claude-code",
        event_type="push_write",
        log_path=log_path,
        record_ids=["risk-score:bad-format"],
    )
    # Event should still be written
    assert log_path.exists()
    event = json.loads(log_path.read_text().strip())
    assert event["schema_version"] == "channel-event/v1"


# ---------------------------------------------------------------------------
# Rotation at 10MB (FR09)
# ---------------------------------------------------------------------------


def test_telemetry_rotation_at_10mb(tmp_path: Path) -> None:
    log_path = tmp_path / "channel-events.jsonl"
    backup_path = log_path.with_suffix(".jsonl.1")

    # Write a file that's just over 10MB
    big_line = "x" * 1024  # 1KB line
    lines_needed = (MAX_EVENTS_BYTES // 1024) + 2
    content = (big_line + "\n") * lines_needed
    log_path.write_text(content, encoding="utf-8")
    assert log_path.stat().st_size > MAX_EVENTS_BYTES

    append_channel_event(
        channel_id="ch",
        client="claude-code",
        event_type="push_write",
        log_path=log_path,
    )

    # Old file should be renamed to .1
    assert backup_path.exists()
    # New file should contain just the new event
    new_content = log_path.read_text(encoding="utf-8").strip()
    event = json.loads(new_content)
    assert event["event_type"] == "push_write"


def test_telemetry_rotation_overwrites_existing_backup(tmp_path: Path) -> None:
    log_path = tmp_path / "channel-events.jsonl"
    backup1 = log_path.with_suffix(".jsonl.1")

    # Create existing backup
    backup1.write_text("old backup\n", encoding="utf-8")

    # Write big log
    big_content = ("a" * 100 + "\n") * (MAX_EVENTS_BYTES // 100 + 5)
    log_path.write_text(big_content, encoding="utf-8")

    append_channel_event(
        channel_id="ch",
        client="claude-code",
        event_type="push_write",
        log_path=log_path,
    )

    # backup1 should have been overwritten (not the old backup content)
    assert backup1.exists()
    # The old ".2" backup must NOT be created
    backup2 = log_path.with_suffix(".jsonl.2")
    assert not backup2.exists()


# ---------------------------------------------------------------------------
# Line cap + prune (FR09)
# ---------------------------------------------------------------------------


def test_telemetry_line_cap_prune(tmp_path: Path) -> None:
    log_path = tmp_path / "channel-events.jsonl"

    # Write MAX_EVENTS_LINES + 1 minimal lines
    line = json.dumps({"k": "v"}) + "\n"
    lines = line * (MAX_EVENTS_LINES + 1)
    log_path.write_text(lines, encoding="utf-8")

    pruned = prune_channel_events(log_path, max_lines=MAX_EVENTS_LINES)

    assert pruned == MAX_EVENTS_LINES + 1 - (MAX_EVENTS_LINES - PRUNE_LINES_ON_CAP)
    remaining = log_path.read_text().splitlines()
    assert len(remaining) == MAX_EVENTS_LINES - PRUNE_LINES_ON_CAP


def test_prune_channel_events_no_op_under_limit(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    log_path.write_text("line1\nline2\n", encoding="utf-8")
    pruned = prune_channel_events(log_path, max_lines=100)
    assert pruned == 0
    assert log_path.read_text() == "line1\nline2\n"


def test_prune_channel_events_missing_file(tmp_path: Path) -> None:
    result = prune_channel_events(tmp_path / "nonexistent.jsonl")
    assert result == 0


def test_prune_channel_events_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "events.jsonl"
    log_path.write_text("line\n" * 60_000, encoding="utf-8")

    def _boom(*_a: object, **_kw: object) -> None:
        raise PermissionError("no write")

    monkeypatch.setattr(os, "rename", _boom)
    # Should not raise
    result = prune_channel_events(log_path, max_lines=50_000)
    # Returns 0 on failure
    assert result == 0


def test_prune_keeps_most_recent_lines(tmp_path: Path) -> None:
    """After prune, the last lines should be the newest ones."""
    log_path = tmp_path / "events.jsonl"
    lines = [f'{{"index": {i}}}\n' for i in range(MAX_EVENTS_LINES + 100)]
    log_path.write_text("".join(lines), encoding="utf-8")

    prune_channel_events(log_path, max_lines=MAX_EVENTS_LINES)
    remaining = log_path.read_text().splitlines()
    # Last line should be from the end (most recent)
    last_event = json.loads(remaining[-1])
    assert last_event["index"] == MAX_EVENTS_LINES + 99


# ---------------------------------------------------------------------------
# TRW_REPO_ROOT env integration
# ---------------------------------------------------------------------------


def test_append_uses_trw_repo_root_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_REPO_ROOT", str(tmp_path))
    append_channel_event(
        channel_id="ch",
        client="claude-code",
        event_type="push_write",
    )
    expected = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
    assert expected.exists()
    monkeypatch.delenv("TRW_REPO_ROOT", raising=False)
