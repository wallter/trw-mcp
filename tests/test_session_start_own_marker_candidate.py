"""After a compaction, ``trw_session_start`` lists the run THIS session captured.

Feedback sub_Rb_lG0AzC1jS2Ike, second half: a session that checkpoints with an
explicit ``run_path`` holds no pin, so the pin-derived ``candidate_runs`` showed
only other sessions' runs. The per-session pre-compact marker is the one record
of "this session captured run X"; it is surfaced as the first advisory
candidate, and never pinned, adopted, or read across sessions.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import extract_tool_fn, make_test_server

_SOURCE = "session_key_pre_compact_marker"


def _use_session(monkeypatch: pytest.MonkeyPatch, session_id: str) -> None:
    """Make *session_id* the process's shared identity ('' = none at all)."""
    from trw_mcp.client_profiles.session_identity import known_session_id_env_vars

    for name in known_session_id_env_vars():
        monkeypatch.delenv(name, raising=False)
    if session_id:
        monkeypatch.setenv("TRW_SESSION_ID", session_id)
    else:
        monkeypatch.delenv("TRW_SESSION_ID", raising=False)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_OFFLINE", "1")
    _use_session(monkeypatch, "sess-mine")
    from trw_mcp.models.config import _reset_config, get_config
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs
    from trw_mcp.tools import ceremony

    _reset_config()
    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    config = get_config()
    (tmp_path / config.runs_root).mkdir(parents=True, exist_ok=True)
    (tmp_path / config.trw_dir / "runtime").mkdir(parents=True, exist_ok=True)
    # Only the step under test: run resolution.
    monkeypatch.setattr(
        ceremony, "SESSION_START_STEPS", tuple(s for s in ceremony.SESSION_START_STEPS if s.key == "run_resolve")
    )
    return tmp_path


def _seed_run(project_root: Path, task: str, run_id: str) -> Path:
    from trw_mcp.models.config import get_config

    run_dir = project_root / get_config().runs_root / task / run_id
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "meta" / "run.yaml").write_text(
        f"run_id: {run_id}\ntask: {task}\nstatus: active\nphase: implement\n", encoding="utf-8"
    )
    (run_dir / "meta" / "events.jsonl").touch()
    return run_dir


def _pre_compact(run: Path, session_id: str) -> None:
    """The real writer: an unpinned session captures *run* by explicit path."""
    checkpoint = extract_tool_fn(make_test_server("orchestration", "checkpoint"), "trw_checkpoint")
    result = checkpoint(
        ctx=SimpleNamespace(session_id=session_id), message="before compaction", pre_compact=True, run_path=str(run)
    )
    assert result["status"] == "success"


def _session_start(session_id: str) -> dict[str, Any]:
    start = extract_tool_fn(make_test_server("ceremony"), "trw_session_start")
    result: dict[str, Any] = start(ctx=SimpleNamespace(session_id=session_id))
    return result


def test_own_marker_run_is_listed_first_and_nothing_is_pinned(project: Path) -> None:
    from trw_mcp.state._paths import pin_active_run
    from trw_mcp.state._pin_store import load_pin_store

    theirs = _seed_run(project, "other", "20261010T000000Z-theirs00")
    pin_active_run(theirs, session_id="sess-other")
    mine = _seed_run(project, "alpha", "20261010T000000Z-mine0000")
    _pre_compact(mine, "sess-mine")
    pins_before = dict(load_pin_store())

    result = _session_start("sess-mine")

    assert result["run"] == {"active_run": None, "status": "no_active_run"}
    assert result["candidate_runs"][0] == {"run_path": str(mine), "source": _SOURCE}
    assert [c["run_path"] for c in result["candidate_runs"][1:]] == [str(theirs)]
    assert "source" not in result["candidate_runs"][1]
    assert _SOURCE in result["hint"]
    # Advisory only: no pin was created and the next call is still unpinned.
    assert dict(load_pin_store()) == pins_before
    assert _session_start("sess-mine")["run"]["status"] == "no_active_run"


def test_own_marker_replaces_a_pin_row_for_the_same_run(project: Path) -> None:
    from trw_mcp.state._paths import pin_active_run

    shared = _seed_run(project, "alpha", "20261010T000000Z-shared00")
    pin_active_run(shared, session_id="sess-other")
    _pre_compact(shared, "sess-mine")

    assert _session_start("sess-mine")["candidate_runs"] == [{"run_path": str(shared), "source": _SOURCE}]


def test_marker_run_that_is_gone_is_not_listed(project: Path) -> None:
    mine = _seed_run(project, "alpha", "20261010T000000Z-gone0000")
    _pre_compact(mine, "sess-mine")
    shutil.rmtree(mine)

    result = _session_start("sess-mine")

    assert "candidate_runs" not in result
    assert _SOURCE not in result["hint"]
    assert str(mine) not in str(result)


def test_marker_path_that_is_no_longer_a_run_is_not_listed(project: Path) -> None:
    mine = _seed_run(project, "alpha", "20261010T000000Z-norun000")
    _pre_compact(mine, "sess-mine")
    (mine / "meta" / "run.yaml").unlink()

    assert "candidate_runs" not in _session_start("sess-mine")


def test_session_without_shared_identity_gets_no_marker_candidate(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback marker is one project-wide file: it may be another session's."""
    _use_session(monkeypatch, "")
    run = _seed_run(project, "alpha", "20261010T000000Z-shared00")
    _pre_compact(run, "ctx-a")
    assert (project / ".trw" / "context" / "pre_compact_state.json").exists()

    result = _session_start("ctx-b")

    assert "candidate_runs" not in result
    assert str(run) not in str(result)


def test_another_sessions_marker_never_appears_as_this_sessions(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    theirs = _seed_run(project, "other", "20261010T000000Z-theirs00")
    _use_session(monkeypatch, "sess-other")
    _pre_compact(theirs, "sess-other")

    _use_session(monkeypatch, "sess-mine")
    result = _session_start("sess-mine")

    assert "candidate_runs" not in result
    assert _SOURCE not in str(result)
    assert str(theirs) not in str(result)


def test_no_marker_leaves_the_response_as_it_was(project: Path) -> None:
    from trw_mcp.state._paths import pin_active_run

    theirs = _seed_run(project, "other", "20261010T000000Z-theirs00")
    pin_active_run(theirs, session_id="sess-other")

    result = _session_start("sess-mine")

    assert [c["run_path"] for c in result["candidate_runs"]] == [str(theirs)]
    assert all("source" not in c for c in result["candidate_runs"])
    assert _SOURCE not in result["hint"]
