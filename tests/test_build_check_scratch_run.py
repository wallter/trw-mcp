"""A build check against a scratch ``run_path`` must not overwrite the project's build state (BUILD-STATUS-SCRATCH-LEAK).

``build-status.yaml`` and the ceremony progress state are PROJECT-level: the deliver gate and ``trw_status`` read
them as the state of this project's own work. canary-eng's VOID check ran ``trw_build_check`` with a scratch run and
left a fake 1-test result there. The run's own receipt is still written; the project state is written only when the
run is the caller's active (pinned) run, or when no run was named.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.state.persistence import FileStateWriter

pytestmark = pytest.mark.integration


def _run_dir(root: Path, name: str) -> Path:
    run = root / name
    (run / "meta").mkdir(parents=True)
    (run / "meta" / "run.yaml").write_text(f"run_id: {name}\nstatus: active\nphase: implement\n", encoding="utf-8")
    (run / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    return run


def _context_bytes(project: Path) -> dict[str, bytes]:
    context = project / ".trw" / "context"
    return {str(p.relative_to(context)): p.read_bytes() for p in sorted(context.rglob("*")) if p.is_file()}


def _seed_project_result(project: Path) -> None:
    (project / ".trw" / "context").mkdir(parents=True, exist_ok=True)
    FileStateWriter().write_yaml(
        project / ".trw" / "context" / "build-status.yaml",
        {"tests_passed": True, "mypy_clean": True, "test_count": 9000, "scope": "the real full suite"},
    )


def test_a_scratch_run_leaves_the_project_build_state_byte_identical(
    tmp_project: Path, tmp_path: Path, build_check_invoke: Any
) -> None:
    _seed_project_result(tmp_project)
    scratch = _run_dir(tmp_path / "scratch", "canary-scratch")
    before = _context_bytes(tmp_project)

    result = build_check_invoke(tests_passed=True, test_count=1, scope="canary fake", run_path=str(scratch))

    assert _context_bytes(tmp_project) == before, "the scratch run's result reached the project-level state"
    assert "cache_path" not in result, "no project cache was written, so none is reported"
    # The run's own receipt is still written.
    assert result["typed_receipt_state"] == "written" and result["build_receipt_id"]
    assert (scratch / "meta" / "receipts" / "build" / f"{result['build_receipt_id']}.json").is_file()


def test_repeated_scratch_reports_never_touch_the_project_state(
    tmp_project: Path, tmp_path: Path, build_check_invoke: Any
) -> None:
    """The second report finds the scratch run already at VALIDATE, a different path through the phase update."""
    _seed_project_result(tmp_project)
    scratch = _run_dir(tmp_path / "scratch", "canary-scratch")
    before = _context_bytes(tmp_project)

    for scope in ("first", "second", "third"):
        build_check_invoke(tests_passed=True, test_count=1, scope=scope, run_path=str(scratch))

    assert _context_bytes(tmp_project) == before


def test_the_pinned_active_run_still_writes_the_project_state(
    tmp_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_check_invoke: Any
) -> None:
    from trw_mcp.state._call_context import build_call_context
    from trw_mcp.state._paths_pin_mgmt import pin_active_run

    _seed_project_result(tmp_project)
    active = _run_dir(tmp_path / "runs", "mine")
    monkeypatch.setenv("TRW_SESSION_ID", "scratch-leak-session")
    pin_active_run(active, context=build_call_context(None))

    result = build_check_invoke(tests_passed=True, test_count=3, scope="my own run", run_path=str(active))

    assert Path(str(result["cache_path"])).is_file()
    cached = (tmp_project / ".trw" / "context" / "build-status.yaml").read_text(encoding="utf-8")
    assert "my own run" in cached and "the real full suite" not in cached


def test_a_different_run_than_the_pinned_one_does_not_write_the_project_state(
    tmp_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_check_invoke: Any
) -> None:
    from trw_mcp.state._call_context import build_call_context
    from trw_mcp.state._paths_pin_mgmt import pin_active_run

    _seed_project_result(tmp_project)
    monkeypatch.setenv("TRW_SESSION_ID", "scratch-leak-session")
    pin_active_run(_run_dir(tmp_path / "runs", "mine"), context=build_call_context(None))
    other = _run_dir(tmp_path / "runs", "someone-elses")
    before = _context_bytes(tmp_project)

    result = build_check_invoke(tests_passed=False, test_count=1, scope="another run", run_path=str(other))

    assert _context_bytes(tmp_project) == before
    assert "cache_path" not in result and result["typed_receipt_state"] == "written"


def test_no_run_path_keeps_the_ordinary_behaviour(tmp_project: Path, build_check_invoke: Any) -> None:
    result = build_check_invoke(tests_passed=True, test_count=2, scope="ordinary")

    assert Path(str(result["cache_path"])).is_file()
    assert "ordinary" in (tmp_project / ".trw" / "context" / "build-status.yaml").read_text(encoding="utf-8")


# --- the caller's OWN record survives (lost-pin sequence) -----------------------------------------------------


def _started_session_with_edits(project: Path, session_id: str, edits: int = 2) -> None:
    """A session that started, edited files, and has no build record: the unpinned deliver gate's input."""
    from trw_mcp.state._ceremony_progress_state import mark_session_started, read_ceremony_state, write_ceremony_state

    trw_dir = project / ".trw"
    mark_session_started(trw_dir, session_id)
    state = read_ceremony_state(trw_dir)
    state.files_modified_since_checkpoint = edits
    write_ceremony_state(trw_dir, state)


def _unpinned_delivery_blocked(project: Path) -> bool:
    from trw_mcp.tools._deliver_gate_selfcomputed import evaluate_build_authority

    return evaluate_build_authority({}, [], None, project / ".trw", False, "")  # type: ignore[arg-type]


def _shared_ceremony_fields(project: Path) -> dict[str, Any]:
    import json

    raw = json.loads((project / ".trw" / "context" / "ceremony-state.json").read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if k not in ("session_build_results", "session_build_results_at")}


def test_lost_pin_build_check_for_the_callers_own_run_releases_its_unpinned_deliver_gate(
    tmp_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_check_invoke: Any
) -> None:
    """Pin lost (compaction, restart): build_check(run_path=own), then trw_deliver() with no run_path."""
    monkeypatch.setenv("TRW_SESSION_ID", "lost-pin-session")
    _started_session_with_edits(tmp_project, "lost-pin-session")
    assert _unpinned_delivery_blocked(tmp_project) is True, "non-vacuity: with no build record this delivery blocks"
    own_run = _run_dir(tmp_path / "runs", "mine")

    build_check_invoke(tests_passed=True, test_count=4, scope="my own run", run_path=str(own_run))

    assert _unpinned_delivery_blocked(tmp_project) is False, "the caller's own passing record was not written"


def test_the_callers_record_is_written_but_no_shared_field_moves(
    tmp_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_check_invoke: Any
) -> None:
    """A canary-style scratch run: build-status.yaml and every shared ceremony field stay byte-identical."""
    monkeypatch.setenv("TRW_SESSION_ID", "canary-session")
    _seed_project_result(tmp_project)
    _started_session_with_edits(tmp_project, "canary-session", edits=0)
    cache_before = (tmp_project / ".trw" / "context" / "build-status.yaml").read_bytes()
    shared_before = _shared_ceremony_fields(tmp_project)
    scratch = _run_dir(tmp_path / "scratch", "canary-scratch")

    build_check_invoke(tests_passed=False, test_count=1, scope="canary fake", run_path=str(scratch))

    assert (tmp_project / ".trw" / "context" / "build-status.yaml").read_bytes() == cache_before
    assert _shared_ceremony_fields(tmp_project) == shared_before, "a shared ceremony field moved for a scratch run"
    import json

    per_session = json.loads((tmp_project / ".trw" / "context" / "ceremony-state.json").read_text(encoding="utf-8"))
    assert per_session["session_build_results"]["canary-session"] == "failed", "the caller's own record is true"


def test_no_ceremony_state_file_is_created_for_a_scratch_run(
    tmp_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_check_invoke: Any
) -> None:
    monkeypatch.setenv("TRW_SESSION_ID", "no-state-session")
    scratch = _run_dir(tmp_path / "scratch", "canary-scratch")

    build_check_invoke(tests_passed=True, test_count=1, scope="canary fake", run_path=str(scratch))

    assert not (tmp_project / ".trw" / "context" / "ceremony-state.json").exists()
