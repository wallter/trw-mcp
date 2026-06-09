"""Tests for ceremony nudge hydration helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tests._ceremony_nudge_support import _trw_dir
from trw_mcp.state.ceremony_nudge import CeremonyState
from trw_mcp.tools._legacy_ceremony_nudge import _hydrate_files_modified


class TestHydrateFilesModified:
    """Tests for _hydrate_files_modified from _session_recall_helpers.py."""

    def test_hydrate_files_modified_counts_events(self, tmp_path: Path) -> None:
        """Events of type 'file_modified' are counted and stored in state."""
        trw = _trw_dir(tmp_path)

        run_dir = tmp_path / ".trw" / "runs" / "task" / "20260101T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        events_path = run_dir / "meta" / "events.jsonl"
        events = [
            {"type": "file_modified", "ts": "2026-01-01T01:00:00Z", "path": "foo.py"},
            {"type": "file_modified", "ts": "2026-01-01T02:00:00Z", "path": "bar.py"},
            {"type": "checkpoint", "ts": "2026-01-01T03:00:00Z"},
            {"type": "file_modified", "ts": "2026-01-01T04:00:00Z", "path": "baz.py"},
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        state = CeremonyState()

        with patch("trw_mcp.state._paths.find_run_via_mtime_scan", return_value=run_dir):
            _hydrate_files_modified(state, trw)

        assert state.files_modified_since_checkpoint == 3

    def test_hydrate_files_modified_respects_checkpoint_ts(self, tmp_path: Path) -> None:
        """Only file_modified events AFTER last_checkpoint_ts are counted."""
        trw = _trw_dir(tmp_path)
        run_dir = tmp_path / ".trw" / "runs" / "task" / "20260201T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        events_path = run_dir / "meta" / "events.jsonl"

        events = [
            {"type": "file_modified", "ts": "2026-01-01T01:00:00Z", "path": "old.py"},
            {"type": "file_modified", "ts": "2026-01-01T02:00:00Z", "path": "old2.py"},
            {"type": "file_modified", "ts": "2026-01-01T04:00:00Z", "path": "new.py"},
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        state = CeremonyState(last_checkpoint_ts="2026-01-01T03:00:00Z")

        with patch("trw_mcp.state._paths.find_run_via_mtime_scan", return_value=run_dir):
            _hydrate_files_modified(state, trw)

        assert state.files_modified_since_checkpoint == 1

    def test_hydrate_files_modified_failopen_no_run(self, tmp_path: Path) -> None:
        """No exception when find_run_via_mtime_scan returns None (no active run)."""
        trw = _trw_dir(tmp_path)
        state = CeremonyState()

        with patch("trw_mcp.state._paths.find_run_via_mtime_scan", return_value=None):
            _hydrate_files_modified(state, trw)

        assert state.files_modified_since_checkpoint == 0

    def test_hydrate_files_modified_failopen_missing_events(self, tmp_path: Path) -> None:
        """No exception when events.jsonl does not exist."""
        trw = _trw_dir(tmp_path)
        run_dir = tmp_path / ".trw" / "runs" / "task" / "20260301T000000Z-noevents"
        (run_dir / "meta").mkdir(parents=True)

        state = CeremonyState()

        with patch("trw_mcp.state._paths.find_run_via_mtime_scan", return_value=run_dir):
            _hydrate_files_modified(state, trw)

        assert state.files_modified_since_checkpoint == 0

    def test_hydrate_files_modified_tolerates_torn_events_line(self, tmp_path: Path) -> None:
        """A torn concurrent append drops one line, not the whole tally.

        events.jsonl is an append-only advisory log here; the file-modified
        count is best-effort. The strict ``read_jsonl`` raised ``StateError`` on
        the first malformed line, which the fail-open wrapper swallowed by
        leaving the count at 0 — so one torn append zeroed the whole tally. The
        resilient reader skips just the torn row, so the intact file_modified
        events are still counted (regression guard).
        """
        trw = _trw_dir(tmp_path)
        run_dir = tmp_path / ".trw" / "runs" / "task" / "20260501T000000Z-torn"
        (run_dir / "meta").mkdir(parents=True)
        events_path = run_dir / "meta" / "events.jsonl"

        intact_a = json.dumps({"type": "file_modified", "ts": "2026-01-01T01:00:00Z", "path": "a.py"})
        torn = '{"type": "file_modified", "ts": "2026-01-01T02:00:00Z", "path": "tor'  # truncated
        intact_b = json.dumps({"type": "file_modified", "ts": "2026-01-01T03:00:00Z", "path": "b.py"})
        events_path.write_text(intact_a + "\n" + torn + "\n" + intact_b + "\n", encoding="utf-8")

        state = CeremonyState()

        with patch("trw_mcp.state._paths.find_run_via_mtime_scan", return_value=run_dir):
            _hydrate_files_modified(state, trw)

        # The torn middle line is dropped; both intact file_modified events count.
        assert state.files_modified_since_checkpoint == 2

    def test_hydrate_files_modified_only_counts_file_modified_type(self, tmp_path: Path) -> None:
        """Events with other types are not counted."""
        trw = _trw_dir(tmp_path)
        run_dir = tmp_path / ".trw" / "runs" / "task" / "20260401T000000Z-mixed"
        (run_dir / "meta").mkdir(parents=True)
        events_path = run_dir / "meta" / "events.jsonl"

        events = [
            {"type": "checkpoint", "ts": "2026-01-01T01:00:00Z"},
            {"type": "tool_invocation", "ts": "2026-01-01T02:00:00Z"},
            {"type": "session_start", "ts": "2026-01-01T03:00:00Z"},
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        state = CeremonyState()

        with patch("trw_mcp.state._paths.find_run_via_mtime_scan", return_value=run_dir):
            _hydrate_files_modified(state, trw)

        assert state.files_modified_since_checkpoint == 0
