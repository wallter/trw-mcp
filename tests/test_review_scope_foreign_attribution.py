"""PRD-QUAL-123: the review-scope count excludes provably-foreign edits, never run-owned ones."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._delivery_helpers import (
    _check_review_file_count_gate,
    _count_file_modified_current_session,
    check_delivery_gates,
)

pytestmark = pytest.mark.integration

ME = "session-me"


def _own(n: int, host: str = "host-me") -> list[dict[str, object]]:
    return [
        {"event": "file_modified", "file": f"docs/own-{i}.md", "session_id": ME, "host_session_id": host}
        for i in range(n)
    ]


def _foreign(n: int, host: str = "host-other") -> list[dict[str, object]]:
    return [
        {"event": "file_modified", "file": f"src/foreign-{i}.py", "session_id": "", "host_session_id": host}
        for i in range(n)
    ]


def _run_dir(tmp_path: Path, events: list[dict[str, object]]) -> Path:
    meta = tmp_path / "docs" / "task" / "runs" / "20260318T120000Z-t" / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text("run_id: r\nstatus: active\nphase: implement\ntask_name: t\n", encoding="utf-8")
    (meta / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return meta.parent


def test_foreign_host_edits_do_not_count_against_run_scope() -> None:
    """The 2026-06-10 shape: 20 owned + 28 foreign counts 20 attributed, not 48."""
    assert _count_file_modified_current_session(_own(20) + _foreign(28), None, ME) == 20


def test_run_owned_edits_still_count_and_block(tmp_path: Path) -> None:
    events = _own(6) + _foreign(3)
    run = _run_dir(tmp_path, events)
    block = _check_review_file_count_gate(run, events, ME)
    assert block is not None and "6 files modified" in block


def test_foreign_edits_do_not_block_delivery(tmp_path: Path) -> None:
    events = _own(2) + _foreign(30)
    run = _run_dir(tmp_path, events)
    assert _check_review_file_count_gate(run, events, ME) is None
    assert "review_scope_block" not in check_delivery_gates(run, FileStateReader(), session_id=ME)


def test_dual_owned_path_counts() -> None:
    """Owned beats foreign: a path both hosts touched is counted."""
    events = [
        *_own(1),
        {"event": "file_modified", "file": "docs/own-0.md", "session_id": "", "host_session_id": "host-other"},
    ]
    assert _count_file_modified_current_session(events, None, ME) == 1


def test_no_ownership_evidence_is_conservative() -> None:
    """Without any own host id nothing is provably foreign, so everything counts."""
    assert _count_file_modified_current_session(_foreign(7), None, ME) == 7


def test_unscoped_caller_counts_everything() -> None:
    assert _count_file_modified_current_session(_own(2) + _foreign(5), None, None) == 7


def test_events_without_host_id_stay_counted() -> None:
    events = [*_own(2), {"event": "file_modified", "file": "x.py", "session_id": ""}]
    assert _count_file_modified_current_session(events, None, ME) == 3


def test_block_message_reports_both_counts(tmp_path: Path) -> None:
    events = _own(6) + _foreign(4)
    run = _run_dir(tmp_path, events)
    block = _check_review_file_count_gate(run, events, ME)
    assert block is not None
    assert "6 files modified (6 session-attributed, 10 whole-run-log)" in block
