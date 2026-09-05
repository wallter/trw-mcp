"""The typed pre-compaction marker: path, shape, reader, atomic writer.

PRD-CORE-258-FR04 + FR07. These tests own the module
``trw_mcp.state.pre_compact_marker`` — the single Python owner of the marker
filename, the marker path, and the marker document.

They live here rather than in ``test_pre_compact_directive_recovery.py`` (the
file the PRD named) because that file was carrying another lane's uncommitted
signature migration at the time this landed, and a shared-checkout commit must
not sweep it. The readback tests the PRD points at are unchanged and still pass
in their original home.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _marker_dir(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    return trw_dir


def test_read_pre_compact_marker_returns_none_on_every_unreadable_shape(tmp_path: Path) -> None:
    """PRD-CORE-258-FR04: absent, non-JSON, non-dict, no timestamp, non-ISO all read as None.

    Each case also names WHY, because "we checked and found nothing" and "we
    never checked" must not be the same answer (PRD-CORE-258-NFR02).
    """
    from trw_mcp.state.pre_compact_marker import (
        pre_compact_marker_path,
        read_pre_compact_marker,
        read_pre_compact_marker_detail,
    )

    trw_dir = _marker_dir(tmp_path)
    marker = pre_compact_marker_path(trw_dir)

    assert read_pre_compact_marker(trw_dir) is None
    assert read_pre_compact_marker_detail(trw_dir).unreadable_reason == "missing_file_race"

    cases = {
        "{not json at all": "invalid_json",
        "[1, 2, 3]": "invalid_json",
        "{}": "missing_timestamp",
        '{"timestamp": ""}': "missing_timestamp",
        '{"timestamp": "unknown"}': "non_iso_timestamp",
        '{"timestamp": "yesterday afternoon"}': "non_iso_timestamp",
    }
    for body, reason in cases.items():
        marker.write_text(body, encoding="utf-8")
        assert read_pre_compact_marker(trw_dir) is None, body
        assert read_pre_compact_marker_detail(trw_dir).unreadable_reason == reason, body


def test_read_pre_compact_marker_returns_the_written_values(tmp_path: Path) -> None:
    """A well-formed marker yields the written timestamp and trigger."""
    from trw_mcp.state.pre_compact_marker import pre_compact_marker_path, read_pre_compact_marker

    trw_dir = _marker_dir(tmp_path)
    pre_compact_marker_path(trw_dir).write_text(
        json.dumps({"timestamp": "2026-09-04T21:49:47.123456+00:00", "trigger": "mcp_tool"}),
        encoding="utf-8",
    )
    marker = read_pre_compact_marker(trw_dir)
    assert marker is not None
    assert marker.timestamp == "2026-09-04T21:49:47.123456+00:00"
    assert marker.trigger == "mcp_tool"


def test_marker_written_by_the_shell_hook_key_ts_is_read(tmp_path: Path) -> None:
    """PRD-CORE-258-FR04: the bundled PreCompact hook writes ``ts``, not ``timestamp``.

    The hook path is the ordinary Claude Code compaction path, so a reader that
    knew only ``timestamp`` would report the most common real marker in
    production as unreadable while passing every fixture-written test.
    """
    from trw_mcp.state.pre_compact_marker import pre_compact_marker_path, read_pre_compact_marker

    trw_dir = _marker_dir(tmp_path)
    marker_path = pre_compact_marker_path(trw_dir)

    marker_path.write_text(json.dumps({"ts": "2026-09-04T21:49:47Z", "trigger": "hook"}), encoding="utf-8")
    hook_marker = read_pre_compact_marker(trw_dir)
    assert hook_marker is not None

    marker_path.write_text(json.dumps({"timestamp": "2026-09-04T21:49:47+00:00"}), encoding="utf-8")
    tool_marker = read_pre_compact_marker(trw_dir)
    assert tool_marker is not None
    assert hook_marker.instant == tool_marker.instant

    # The hook writes the literal ``unknown`` when ``date`` fails: not an instant.
    marker_path.write_text(json.dumps({"ts": "unknown"}), encoding="utf-8")
    assert read_pre_compact_marker(trw_dir) is None


def test_marker_path_resolves_with_and_without_an_explicit_trw_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The filename literal lives in one constant, and the default is late-bound."""
    from trw_mcp.state.pre_compact_marker import PRE_COMPACT_MARKER_FILENAME, pre_compact_marker_path

    trw_dir = tmp_path / ".trw"
    assert pre_compact_marker_path(trw_dir) == trw_dir / "context" / PRE_COMPACT_MARKER_FILENAME
    # Late binding is load-bearing: every gate test patches the DEFINITION site.
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    assert pre_compact_marker_path() == trw_dir / "context" / PRE_COMPACT_MARKER_FILENAME


def test_marker_model_ignores_the_extra_keys_the_writer_emits(tmp_path: Path) -> None:
    """The real writer emits a dozen unmodelled keys; validation must not care."""
    from trw_mcp.state.pre_compact_marker import pre_compact_marker_path, read_pre_compact_marker

    trw_dir = _marker_dir(tmp_path)
    pre_compact_marker_path(trw_dir).write_text(
        json.dumps(
            {
                "timestamp": "2026-09-04T21:49:47+00:00",
                "trigger": "mcp_tool",
                "run_path": "/tmp/run",
                "phase": "implement",
                "events_logged": 3,
                "last_checkpoint": "wrote the reader",
                "prd_scope": ["PRD-CORE-258"],
                "last_5_events": [],
                "failing_tests": [],
                "ceremony_state": {},
                "pending_ceremony": [],
                "directive": "finish FR04",
                "context_anchor": "reader wired",
            }
        ),
        encoding="utf-8",
    )
    marker = read_pre_compact_marker(trw_dir)
    assert marker is not None
    assert marker.phase == "implement"
    assert marker.directive == "finish FR04"
    assert marker.context_anchor == "reader wired"


# --- PRD-CORE-258-FR07: the writer is atomic ---


def test_marker_write_is_atomic_no_reader_sees_a_partial_file(tmp_path: Path) -> None:
    """A reader polling across repeated writes never sees an existing, unparseable file.

    The old writer truncated in place, so the window between truncation and
    completion showed a file that EXISTS — which is what arms the gate — and
    whose JSON does not parse.
    """
    import threading

    from trw_mcp.state.pre_compact_marker import pre_compact_marker_path, write_pre_compact_marker

    trw_dir = _marker_dir(tmp_path)
    path = pre_compact_marker_path(trw_dir)
    body = {"timestamp": "2026-09-04T21:49:47+00:00", "filler": "x" * 200_000}
    write_pre_compact_marker(body, trw_dir)

    decode_errors: list[str] = []
    stop = threading.Event()

    def _reader() -> None:
        while not stop.is_set():
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                decode_errors.append("marker vanished mid-write")
                continue
            try:
                json.loads(text)
            except ValueError:
                decode_errors.append(text[:40])

    poller = threading.Thread(target=_reader)
    poller.start()
    try:
        for index in range(60):
            write_pre_compact_marker({**body, "n": index}, trw_dir)
    finally:
        stop.set()
        poller.join(timeout=5)

    assert decode_errors == [], f"a reader observed a partial marker: {decode_errors[:3]}"
    assert list(path.parent.glob("*.tmp")) == [], "no temporary sibling may survive a successful write"


def test_a_failed_write_leaves_the_previous_marker_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A raise after the temp file is created must not destroy the prior document."""
    from trw_mcp.state import pre_compact_marker as module

    trw_dir = _marker_dir(tmp_path)
    path = module.pre_compact_marker_path(trw_dir)
    module.write_pre_compact_marker({"timestamp": "2026-09-04T21:49:47+00:00", "trigger": "first"}, trw_dir)

    def _explode(src: object, dst: object) -> None:
        raise OSError("promotion failed")

    monkeypatch.setattr(module.os, "replace", _explode)
    with pytest.raises(OSError, match="promotion failed"):
        module.write_pre_compact_marker({"timestamp": "2026-09-04T22:00:00+00:00", "trigger": "second"}, trw_dir)

    survivor = module.read_pre_compact_marker(trw_dir)
    assert survivor is not None
    assert survivor.trigger == "first", "the previous complete document must survive"
    assert list(path.parent.glob("*.tmp")) == [], "a failed write must leave no temporary sibling"


# --- PRD-CORE-258-FR10: the writer stamps the marker's owner ---


def test_the_writer_stamps_the_owner_pin_key_and_a_diagnostic_pid(tmp_path: Path) -> None:
    """The marker names WHO compacted, so only that session is armed by it.

    The identity is a pin key, not a FastMCP ``session_id``: PRD-FIX-118 built
    ``resolve_pin_key`` and the hook helper ``trw_pin_key`` to resolve the same
    string, which is the one identifier an MCP server and a shell hook can both
    observe.
    """
    import os

    from trw_mcp.state.pre_compact_marker import read_pre_compact_marker
    from trw_mcp.tools.checkpoint import _write_compact_state

    run_dir = tmp_path / "run" / "abc"
    (run_dir / "meta").mkdir(parents=True)
    (tmp_path / ".trw" / "context").mkdir(parents=True)
    _write_compact_state(
        tmp_path,
        run_dir,
        run_dir / "meta" / "events.jsonl",
        prd_scope=[],
        phase="implement",
        formation="none active",
        failing_tests=[],
        ceremony_state={},
        owner_pin_key="pin-owner",
    )

    marker = read_pre_compact_marker(tmp_path / ".trw")
    assert marker is not None
    assert marker.owner_pin_key == "pin-owner"
    assert marker.owner_pid == os.getpid()


def test_an_omitted_owner_writes_no_owner_field_at_all(tmp_path: Path) -> None:
    """RISK-006: an ownerless marker keeps the fail-safe blanket behaviour."""
    from trw_mcp.state.pre_compact_marker import pre_compact_marker_path, read_pre_compact_marker
    from trw_mcp.tools.checkpoint import _write_compact_state

    run_dir = tmp_path / "run" / "abc"
    (run_dir / "meta").mkdir(parents=True)
    (tmp_path / ".trw" / "context").mkdir(parents=True)
    _write_compact_state(
        tmp_path,
        run_dir,
        run_dir / "meta" / "events.jsonl",
        prd_scope=[],
        phase="implement",
        formation="none active",
        failing_tests=[],
        ceremony_state={},
    )

    raw = json.loads(pre_compact_marker_path(tmp_path / ".trw").read_text(encoding="utf-8"))
    assert "owner_pin_key" not in raw
    assert "owner_pid" not in raw
    marker = read_pre_compact_marker(tmp_path / ".trw")
    assert marker is not None
    assert marker.owner_pin_key == ""
