"""PRD-CORE-329 FR03 — trw_session_start surfaces the same pending decision first.

Mirrors ``test_status_blocked_decision.py``'s FR02 assertions for the second
reader of the project-level queue: ``trw_session_start`` places
``blocked_decision`` ahead of the ``learnings`` stub list it builds during its
own recall step, and omits the key entirely when nothing is pending.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import get_tools_sync, make_test_server
from trw_mcp.models.config import _reset_config
from trw_mcp.state._paths import pin_active_run, unpin_active_run

pytestmark = pytest.mark.integration


def _session_start_fn():
    server = make_test_server("ceremony")
    tools = get_tools_sync(server)
    return tools["trw_session_start"].fn


@pytest.fixture
def pinned_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "runtime").mkdir(parents=True)
    run_dir = trw_dir / "runs" / "task" / "run-123"
    meta_dir = run_dir / "meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "run.yaml").write_text(
        "run_id: run-123\nstatus: active\nphase: implement\ntask: task\nowner_session_id: sess-329\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("TRW_SESSION_ID", "sess-329")
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", lambda: trw_dir)
    pin_active_run(run_dir, session_id="sess-329")
    _reset_config(None)
    yield trw_dir, run_dir
    unpin_active_run(session_id="sess-329")
    _reset_config(None)


def test_session_start_surfaces_blocked_decision_first(pinned_run: tuple[Path, Path]) -> None:
    trw_dir, run_dir = pinned_run
    from trw_mcp.state._decision_queue import record_decision

    decision_id = record_decision(
        trw_dir,
        run_path=str(run_dir),
        question="Delete the legacy table?",
        options=["delete", "keep"],
        why_unreachable="unattended loop, no operator online",
    )

    result = _session_start_fn()(query="")

    keys = list(result.keys())
    assert "blocked_decision" in keys
    assert "learnings" in keys
    assert keys.index("blocked_decision") < keys.index("learnings")
    assert result["blocked_decision"]["id"] == decision_id


def test_session_start_omits_blocked_decision_when_none_pending(pinned_run: tuple[Path, Path]) -> None:
    result = _session_start_fn()(query="")
    assert "blocked_decision" not in result
    assert "blocked_decisions_pending" not in result
