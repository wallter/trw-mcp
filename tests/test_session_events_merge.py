"""Resilience tests for _merge_session_events advisory session-event merge.

session-events.jsonl is an append-only advisory log written before trw_init
creates the run directory. A single torn/undecodable concurrent append must
degrade to "drop that line", not "drop the entire session-events merge".
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state._session_events import _merge_session_events


def _write(path: Path, *lines: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


def test_merge_prepends_session_events(tmp_path: Path) -> None:
    """Well-formed session events are prepended ahead of run events."""
    trw_dir = tmp_path / ".trw"
    _write(
        trw_dir / "context" / "session-events.jsonl",
        '{"event": "session_start", "ts": "2026-02-19T09:00:00Z"}',
    )
    run_events: list[dict[str, object]] = [{"event": "run_init", "ts": "2026-02-19T10:00:00Z"}]

    merged = _merge_session_events(run_events, trw_dir)

    assert [e["event"] for e in merged] == ["session_start", "run_init"]


def test_merge_missing_file_returns_run_events(tmp_path: Path) -> None:
    """Absent session-events.jsonl leaves run events untouched."""
    run_events: list[dict[str, object]] = [{"event": "run_init"}]

    assert _merge_session_events(run_events, tmp_path / ".trw") == run_events


def test_merge_skips_torn_line_keeps_valid_session_events(tmp_path: Path) -> None:
    """A torn final append drops only that line, not the whole merge."""
    trw_dir = tmp_path / ".trw"
    _write(
        trw_dir / "context" / "session-events.jsonl",
        '{"event": "session_start", "ts": "2026-02-19T09:00:00Z"}',
        '{"event": "recall", "ts": "2026-02-19T09:01:0',  # torn append
    )
    run_events: list[dict[str, object]] = [{"event": "run_init"}]

    merged = _merge_session_events(run_events, trw_dir)

    # The valid session event survives; previously a strict read raised and the
    # except-clause dropped ALL session events.
    assert [e["event"] for e in merged] == ["session_start", "run_init"]


def test_merge_skips_non_object_and_undecodable_lines(tmp_path: Path) -> None:
    """Bare-value JSON and non-UTF-8 rows are skipped per-line."""
    trw_dir = tmp_path / ".trw"
    path = trw_dir / "context" / "session-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(b'{"event": "session_start"}\n')
        fh.write(b"42\n")  # valid JSON, non-object
        fh.write(b"\xff\xfe bad bytes\n")  # undecodable
        fh.write(b'{"event": "recall"}\n')
    run_events: list[dict[str, object]] = [{"event": "run_init"}]

    merged = _merge_session_events(run_events, trw_dir)

    assert [e["event"] for e in merged] == ["session_start", "recall", "run_init"]
