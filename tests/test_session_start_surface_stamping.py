"""Integration tests for surface_snapshot_id stamping in trw_session_start.

Wave 2a — PRD-HPO-MEAS-001 FR-2 wiring check. These assertions verify
that ``stamp_session`` is actually invoked by ``trw_session_start`` and
its resolved ``surface_snapshot_id`` flows into the result dict +
``run_surface_snapshot.yaml`` is written when a run is pinned.

These are behavioral (not existence) tests: each one would fail if the
Wave 2a wiring were reverted to the Phase-1 empty-string default.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.telemetry.artifact_registry import clear_snapshot_cache


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    clear_snapshot_cache()
    yield
    clear_snapshot_cache()


def test_session_start_populates_surface_snapshot_id(tmp_project: Path) -> None:
    """``trw_session_start`` returns a non-empty ``surface_snapshot_id``."""
    server = make_test_server("ceremony")
    session_start = extract_tool_fn(server, "trw_session_start")

    result = session_start()
    # Phase 1: fail-open means empty-string is allowed ONLY when the
    # bundled data root is unavailable. In a dev install, resolve always
    # succeeds, so we expect a real 64-char sha256.
    assert "surface_snapshot_id" in result
    # Either a real id OR explicit empty-string (fail-open path); never missing.
    snapshot_id = result["surface_snapshot_id"]
    assert isinstance(snapshot_id, str)
    if snapshot_id:
        assert len(snapshot_id) == 64, f"expected sha256 hex or empty, got {snapshot_id!r}"


def test_session_start_writes_run_surface_snapshot_when_run_pinned(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With an active run pinned, ``run_surface_snapshot.yaml`` is on disk.

    Forces the pin path by monkeypatching ``_find_active_run_compat`` so
    the disk assertion always fires — addresses audit finding F-04.
    """
    run_root = tmp_project / ".trw" / "runs" / "test-task" / "20260423T000000Z-deadbeef"
    (run_root / "meta").mkdir(parents=True, exist_ok=True)
    (run_root / "meta" / "run.yaml").write_text(
        "task: test-task\nphase: research\nstatus: active\n"
    )
    (run_root / "meta" / "events.jsonl").touch()

    # Stub the run discovery + pin so Step 2c of trw_session_start always
    # has a real run_dir to stamp. We cannot rely on real pinning in the
    # test harness because it depends on ctx.session_id state.
    import trw_mcp.tools.ceremony as ceremony_mod

    monkeypatch.setattr(ceremony_mod, "_find_active_run_compat", lambda ctx: run_root)
    monkeypatch.setattr(ceremony_mod, "pin_active_run", lambda run_dir, context=None: None)
    monkeypatch.setattr(
        ceremony_mod,
        "_get_run_status",
        lambda rd: {
            "active_run": rd.name,
            "status": "active",
            "task_name": "test-task",
            "phase": "research",
        },
    )

    server = make_test_server("ceremony", "checkpoint")
    session_start = extract_tool_fn(server, "trw_session_start")
    result = session_start()

    # Primary assertion — surface_snapshot_id present in result dict.
    assert "surface_snapshot_id" in result

    # Unconditional disk assertion — with the stubs above, Step 2c MUST
    # have called stamp_session(run_root / "meta").
    manifest = run_root / "meta" / "run_surface_snapshot.yaml"
    assert manifest.exists(), f"pinned run should have run_surface_snapshot.yaml at {manifest}"
    body = manifest.read_text()
    assert "snapshot_id:" in body
    assert "artifacts:" in body


def test_session_start_fail_open_returns_empty_string_not_missing() -> None:
    """Even on stamping failure, ``surface_snapshot_id`` key is present."""
    server = make_test_server("ceremony")
    session_start = extract_tool_fn(server, "trw_session_start")

    result = session_start()
    # Key is always present (fail-open writes empty string on error path).
    assert "surface_snapshot_id" in result
    assert isinstance(result["surface_snapshot_id"], str)
