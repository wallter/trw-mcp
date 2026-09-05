"""WD-03 — an UNREADABLE run event log is uncomputable evidence, not zero changes.

External audit + independent skeptic (2026-09-04). ``_read_run_events``
collapsed every read failure to ``[]``. Two gates then counted 0 modified files
from that empty list and allowed the delivery:

- ``count_session_changed_files`` returned ``0`` without raising, so
  ``resolve_deliver_gate_decision``'s fail-closed ``None`` branch (PRD-CORE-246
  NFR02) was never reached — the branch existed and was unreachable.
- ``_check_review_file_count_gate`` compared ``0 > 5`` and never fired the
  >N-file review-scope hard block.

Reproduction the audit reported: an events.jsonl that cannot be read (crash
mid-write leaving a path that is not a readable regular file) + a
``task_type`` outside ``{coding, rca, eval}`` + dozens of real edits ->
delivery ALLOWED. These tests drive the production ``check_delivery_gates``
path so they go red if the ``None`` propagation is reverted at any hop.

The counterpart non-vacuity assertions matter as much: an ABSENT log is still
an honest ``[]`` and must NOT be reported as unreadable, or the fix would just
be "block everything".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._delivery_helpers import (
    _check_build_and_work_events,
    _check_review_file_count_gate,
    _read_run_events,
    check_delivery_gates,
    count_session_changed_files,
)

pytestmark = pytest.mark.integration


def _make_run(tmp_path: Path, task_type: str, *, modified: int) -> Path:
    writer = FileStateWriter()
    run_dir = tmp_path / ".trw" / "runs" / "wd03" / "20260904T000000Z-aaaa1111"
    (run_dir / "meta").mkdir(parents=True)
    writer.write_yaml(
        run_dir / "meta" / "run.yaml",
        {
            "run_id": "20260904T000000Z-aaaa1111",
            "task": "wd03",
            "status": "active",
            "phase": "deliver",
            "task_type": task_type,
        },
    )
    for i in range(modified):
        writer.append_jsonl(run_dir / "meta" / "events.jsonl", {"event": "file_modified", "file": f"src/m{i}.py"})
    return run_dir


def _make_unreadable(run_dir: Path) -> Path:
    """Replace events.jsonl with a path that exists but cannot be read.

    A directory is the portable choice: ``reader.exists`` is True (so the
    "absent log" branch is NOT what is being exercised) while the read raises.
    ``chmod 000`` would be a no-op for a root test runner.
    """
    events_path = run_dir / "meta" / "events.jsonl"
    if events_path.exists():
        events_path.unlink()
    events_path.mkdir()
    return events_path


def test_read_run_events_distinguishes_unreadable_from_empty(tmp_project: Path) -> None:
    """The three outcomes of ``_read_run_events`` are three distinct values."""
    reader = FileStateReader()
    run_dir = _make_run(tmp_project, "docs", modified=2)

    assert _read_run_events(run_dir, reader) is not None  # readable

    absent = tmp_project / ".trw" / "runs" / "wd03" / "absent"
    (absent / "meta").mkdir(parents=True)
    assert _read_run_events(absent, reader) == []  # honest empty, NOT None

    _make_unreadable(run_dir)
    assert _read_run_events(run_dir, reader) is None  # uncomputable


def test_count_session_changed_files_is_none_when_events_unreadable(tmp_project: Path) -> None:
    """The count is UNCOMPUTABLE, never 0 — that is what arms the fail-closed branch."""
    run_dir = _make_run(tmp_project, "docs", modified=3)
    assert count_session_changed_files(events=None, run_path=run_dir, session_id=None) is None
    assert count_session_changed_files(events=[], run_path=run_dir, session_id=None) == 0


def test_review_scope_gate_blocks_and_names_the_unreadable_log(tmp_project: Path) -> None:
    """R-01 fires on an unreadable log and names the file the operator must repair."""
    run_dir = _make_run(tmp_project, "docs", modified=50)
    events_path = _make_unreadable(run_dir)

    block = _check_review_file_count_gate(run_dir, None, None)
    assert block is not None
    assert str(events_path) in block

    # Non-vacuity: an honestly empty log does NOT block.
    assert _check_review_file_count_gate(run_dir, [], None) is None


def test_review_scope_gate_yields_to_a_substantive_review(tmp_project: Path) -> None:
    """Direct review evidence still satisfies the gate even with an unreadable log."""
    run_dir = _make_run(tmp_project, "docs", modified=50)
    _make_unreadable(run_dir)
    FileStateWriter().write_yaml(
        run_dir / "meta" / "review.yaml",
        {"verdict": "pass", "substantive": True, "summary": "reviewed"},
    )
    assert _check_review_file_count_gate(run_dir, None, None) is None


def test_build_gate_reports_unreadable_log_separately_from_empty() -> None:
    """The unreadable message names the log; the honest-empty message does not."""
    unreadable, _ = _check_build_and_work_events(None)
    empty, _ = _check_build_and_work_events([])
    assert unreadable is not None and "events.jsonl" in unreadable
    assert empty is not None and "events.jsonl" not in empty


def test_audit_repro_docs_task_with_unreadable_log_is_blocked(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported repro, end to end: docs task + 50 real edits + torn log -> BLOCKED.

    Pre-fix this returned no ``delivery_blocked`` and no ``review_scope_block``:
    the unreadable log measured 0 changed files, ``docs`` is outside
    ``_BUILD_ARTIFACT_TASK_TYPES``, and both gates therefore stood down.
    """
    cfg = TRWConfig().model_copy(update={"deliver_gate_mode": "block_coding"})
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "docs", modified=50)
    _make_unreadable(run_dir)

    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")

    assert result.get("delivery_blocked"), result
    assert result.get("blocked_task_type") == "docs"
    assert result.get("missing_gate") == "build_check"
    assert result.get("review_scope_block")
    # The soft warning is kept, not replaced by the block.
    assert result.get("build_gate_warning")
