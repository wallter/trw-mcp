"""``trw_checkpoint(pre_compact=True, run_path=...)`` records to the named run.

Feedback sub_Rb_lG0AzC1jS2Ike: a session with no pinned run (the state a
context compaction leaves behind) passed an explicit, valid ``run_path`` with
``pre_compact=True`` and was answered ``skipped`` / ``no_active_run``, while the
same call without ``pre_compact`` recorded. The pre-compact mode never received
``run_path``. These tests pin the repaired behaviour and that the explicit path
is validated exactly as the ordinary checkpoint validates it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.exceptions import StateError

_IGNORED_DIRS = ("logs", "runtime")


def _snapshot(root: Path) -> dict[str, str]:
    snap: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and not any(part in _IGNORED_DIRS for part in rel.parts):
            snap[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snap


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Project root one level below ``tmp_path`` so ``tmp_path/outside`` escapes it."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("TRW_OFFLINE", "1")
    monkeypatch.setenv("TRW_SESSION_ID", "sess-compacted")
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project_root)
    monkeypatch.setattr("trw_mcp.tools.checkpoint.resolve_project_root", lambda: project_root)
    from trw_mcp.models.config import _reset_config, get_config
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs

    _reset_config()
    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    config = get_config()
    (project_root / config.runs_root).mkdir(parents=True, exist_ok=True)
    (project_root / config.trw_dir / "runtime").mkdir(parents=True, exist_ok=True)
    return project_root


def _seed_run(project_root: Path, task: str, run_id: str) -> Path:
    from trw_mcp.models.config import get_config

    run_dir = project_root / get_config().runs_root / task / run_id
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "meta" / "run.yaml").write_text(
        f"run_id: {run_id}\ntask: {task}\nstatus: active\nphase: implement\n", encoding="utf-8"
    )
    (run_dir / "meta" / "events.jsonl").touch()
    return run_dir


def _checkpoint() -> Any:
    return extract_tool_fn(make_test_server("orchestration", "checkpoint"), "trw_checkpoint")


def _ctx(session_id: str = "sess-compacted") -> Any:
    return SimpleNamespace(session_id=session_id)


def _messages(run_dir: Path) -> list[str]:
    path = run_dir / "meta" / "checkpoints.jsonl"
    if not path.exists():
        return []
    return [str(json.loads(line)["message"]) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _marker(project_root: Path) -> dict[str, Any]:
    from trw_mcp.state.pre_compact_marker import pre_compact_marker_path

    path = pre_compact_marker_path(project_root / ".trw")
    assert path.exists(), "pre_compact=True reported success but wrote no marker for this session"
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def test_unpinned_session_with_explicit_run_path_records_and_marks_that_run(project: Path) -> None:
    run = _seed_run(project, "alpha", "20261010T000000Z-aaaa1111")

    result = _checkpoint()(
        ctx=_ctx(),
        message="about to compact",
        pre_compact=True,
        run_path=str(run),
        directive="finish the fix",
    )

    assert result.get("reason") != "no_active_run"
    assert result["status"] == "success"
    assert result["run_path"] == str(run)
    # The caller's message is recorded as an ordinary checkpoint, then the capture's own line.
    assert _messages(run) == ["about to compact", "pre-compaction safety checkpoint"]
    marker = _marker(project)
    assert marker["run_path"] == str(run)
    assert marker["directive"] == "finish the fix"


def test_explicit_run_path_wins_over_the_sessions_pin(project: Path) -> None:
    """Same precedence as the ordinary checkpoint: the named run, not the pinned one."""
    from trw_mcp.state._call_context import build_call_context
    from trw_mcp.state._paths import pin_active_run

    pinned = _seed_run(project, "alpha", "20261010T000000Z-pinned00")
    named = _seed_run(project, "beta", "20261010T000000Z-named000")
    pin_active_run(pinned, context=build_call_context(_ctx()))

    result = _checkpoint()(ctx=_ctx(), message="m", pre_compact=True, run_path=str(named))

    assert result["run_path"] == str(named)
    assert _messages(named) == ["m", "pre-compaction safety checkpoint"]
    assert _messages(pinned) == []
    assert _marker(project)["run_path"] == str(named)


def _refusal(project: Path, run_path: str, *, pre_compact: bool, message: str = "m") -> str:
    before = _snapshot(project)
    with pytest.raises(StateError) as excinfo:
        _checkpoint()(ctx=_ctx(), message=message, pre_compact=pre_compact, run_path=run_path)
    assert _snapshot(project) == before, "a refused run_path must write nothing"
    return str(excinfo.value)


@pytest.mark.parametrize("kind", ["escapes_project", "missing", "not_a_run"])
def test_bad_explicit_run_path_is_refused_exactly_as_the_ordinary_checkpoint_refuses_it(
    project: Path, kind: str
) -> None:
    if kind == "escapes_project":
        target = project.parent / "outside" / "20261010T000000Z-foreign0"
        (target / "meta").mkdir(parents=True)
        (target / "meta" / "run.yaml").write_text("run_id: foreign\nstatus: active\n", encoding="utf-8")
    elif kind == "missing":
        target = project / ".trw" / "runs" / "alpha" / "no-such-run"
    else:
        target = project / "src"
        target.mkdir()

    ordinary = _refusal(project, str(target), pre_compact=False)
    pre_compact = _refusal(project, str(target), pre_compact=True)
    # With no message the capture resolves the path itself, not the ordinary checkpoint: same refusal.
    capture_alone = _refusal(project, str(target), pre_compact=True, message="")

    assert pre_compact == ordinary == capture_alone
    assert not (target / "meta" / "checkpoints.jsonl").exists()
    if kind == "escapes_project":
        assert "escapes project root" in pre_compact
        assert str(target) not in pre_compact
    if kind == "not_a_run":
        assert not (target / "meta").exists()


def test_pinned_session_without_run_path_is_unchanged(project: Path) -> None:
    from trw_mcp.state._call_context import build_call_context
    from trw_mcp.state._paths import pin_active_run

    run = _seed_run(project, "alpha", "20261010T000000Z-pinned00")
    pin_active_run(run, context=build_call_context(_ctx()))

    result = _checkpoint()(ctx=_ctx(), pre_compact=True)

    assert result["status"] == "success"
    assert result["run_path"] == str(run)
    assert _messages(run) == ["pre-compaction safety checkpoint"]
    assert _marker(project)["run_path"] == str(run)


def test_unpinned_session_without_run_path_still_skips_and_writes_nothing(project: Path) -> None:
    _seed_run(project, "alpha", "20261010T000000Z-other000")
    before = _snapshot(project)

    result = _checkpoint()(ctx=_ctx(), pre_compact=True)

    assert result == {"status": "skipped", "reason": "no_active_run"}
    assert _snapshot(project) == before


def test_a_message_with_no_run_is_refused_as_the_ordinary_checkpoint_refuses_it(project: Path) -> None:
    """The message is a checkpoint like any other, so it gets that path's answer (and its remedy), not a skip."""
    _seed_run(project, "alpha", "20261010T000000Z-other000")
    before = _snapshot(project)

    ordinary = _checkpoint()(ctx=_ctx(), message="m")
    result = _checkpoint()(ctx=_ctx(), message="m", pre_compact=True)

    assert (result["recorded"], result["reason"]) == (False, "no_active_run")
    assert (result["reason"], result["remedy"]) == (ordinary["reason"], ordinary["remedy"])
    assert result.get("status") != "success"
    assert _snapshot(project) == before


def test_a_message_the_ordinary_checkpoint_refuses_never_enters_the_journal_through_pre_compact(project: Path) -> None:
    """Review, 2026-10-11: forwarding the message into the capture would have bypassed the ordinary path's
    refusals. A factory transition is refused while the experiment is off; pre_compact must not change that."""
    run = _seed_run(project, "alpha", "20261010T000000Z-factory0")
    factory = json.dumps({"factory": 1, "kind": "START", "attempt": "a1"})
    before = _snapshot(project)

    ordinary = _checkpoint()(ctx=_ctx(), message=factory, run_path=str(run))
    result = _checkpoint()(ctx=_ctx(), message=factory, pre_compact=True, run_path=str(run))

    assert ordinary["recorded"] is False, "non-vacuity: the ordinary path refuses this message"
    assert (result["recorded"], result["reason"]) == (False, ordinary["reason"])
    assert _messages(run) == [] and _snapshot(project) == before


def test_a_pin_lookup_that_fails_is_a_failed_capture_not_an_exception(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review, 2026-10-11: only an explicit run_path may raise; the pin lookup stays best-effort."""

    def broken(**_kwargs: object) -> Path:
        raise OSError("pin store unreadable")

    monkeypatch.setattr("trw_mcp.tools.checkpoint.find_active_run", broken)

    result = _checkpoint()(ctx=_ctx(), pre_compact=True)

    assert result["status"] == "failed" and "pin store unreadable" in result["error"]


# --- the caller's message (PRD-CORE-342 FR-02: the marker carries the real last message) ---


def test_non_blank_message_is_recorded_and_carried_by_the_marker(project: Path) -> None:
    run = _seed_run(project, "alpha", "20261010T000000Z-msg00000")

    result = _checkpoint()(
        ctx=_ctx(),
        message="FR-08 wired; next: parity test",
        pre_compact=True,
        run_path=str(run),
        directive="land FR-08",
        context_anchor="orchestration.py",
    )

    assert _messages(run) == ["FR-08 wired; next: parity test", "pre-compaction safety checkpoint"]
    marker = _marker(project)
    assert marker["last_checkpoint"] == "FR-08 wired; next: parity test"
    # directive / context_anchor behaviour is untouched by the message.
    assert (marker["directive"], marker["context_anchor"]) == ("land FR-08", "orchestration.py")
    assert (result["directive"], result["context_anchor"]) == ("land FR-08", "orchestration.py")
    assert set(result) == {
        "status",
        "run_path",
        "compact_instructions_path",
        "prd_scope",
        "failing_tests",
        "directive",
        "context_anchor",
        "checkpoint",
    }
    assert result["checkpoint"]["recorded"] is True


@pytest.mark.parametrize("blank", ["", "   \n"])
def test_blank_message_keeps_the_generic_text(project: Path, blank: str) -> None:
    run = _seed_run(project, "alpha", "20261010T000000Z-blank000")

    result = _checkpoint()(ctx=_ctx(), message=blank, pre_compact=True, run_path=str(run))

    assert result["status"] == "success"
    assert _messages(run) == ["pre-compaction safety checkpoint"]
    marker = _marker(project)
    assert marker["last_checkpoint"] == "pre-compaction safety checkpoint"
    assert "directive" not in marker
    assert "directive" not in result


def test_a_blank_message_after_a_real_checkpoint_leaves_the_real_one_in_the_marker(project: Path) -> None:
    """The PreCompact hook gives no message. The session resumes from the marker, so it must name the last thing
    the agent recorded, not the line this very call appended (PRD-CORE-342 FR-02)."""
    run = _seed_run(project, "alpha", "20261010T000000Z-real0000")
    assert _checkpoint()(ctx=_ctx(), message="cache fix committed; next: review", run_path=str(run))["status"]

    result = _checkpoint()(ctx=_ctx(), message="", pre_compact=True, run_path=str(run))

    assert result["status"] == "success"
    assert _messages(run) == ["cache fix committed; next: review", "pre-compaction safety checkpoint"]
    assert _marker(project)["last_checkpoint"] == "cache fix committed; next: review"


def test_a_recorded_message_is_reported_even_when_the_capture_is_switched_off(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review, 2026-10-11: two writes, two outcomes. The capture can be skipped after the message is in the
    journal, and an answer of only "skipped" would say nothing was saved."""
    from trw_mcp.models.config import get_config

    run = _seed_run(project, "alpha", "20261010T000000Z-off00000")
    monkeypatch.setattr(get_config(), "auto_checkpoint_pre_compact", False)

    result = _checkpoint()(ctx=_ctx(), message="saved before compaction", pre_compact=True, run_path=str(run))

    assert _messages(run) == ["saved before compaction"]
    assert result["status"] == "skipped"
    assert result["checkpoint"]["recorded"] is True


def test_a_blocked_decision_recorded_with_pre_compact_keeps_its_id_in_the_answer(project: Path) -> None:
    run = _seed_run(project, "alpha", "20261010T000000Z-decide00")

    result = _checkpoint()(
        ctx=_ctx(),
        message="waiting on a decision",
        pre_compact=True,
        run_path=str(run),
        blocked_decision={"question": "ship or hold?", "options": ["ship", "hold"], "why_unreachable": "asleep"},
    )

    assert result["status"] == "success"
    assert result["checkpoint"]["recorded"] is True
    assert result["checkpoint"]["blocked_decision_id"]
