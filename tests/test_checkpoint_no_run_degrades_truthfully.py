"""PRD-CORE-233 FR02 / NFR04 — trw_checkpoint degrades truthfully, never dishonestly.

A context-aware caller with no pinned run and no explicit ``run_path`` used to
get a ``StateError`` through FastMCP; the 7 bundled sub-agents that hold
``trw_checkpoint`` without ``trw_init`` could only report it as a tool failure.
FR02 replaces exactly that branch with a structured not-recorded result.

NFR04 is the constraint that makes the softening honest rather than a silent
swallow: a checkpoint that was NOT persisted must never look like one — not in
the response, and not in the ceremony progress counters the deliver gate reads.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests._tools_orchestration_support import orch_tools  # noqa: F401

_IGNORED_DIRS = ("logs", "runtime")


def _snapshot(root: Path) -> dict[str, str]:
    """Content-hash every file under *root*, skipping volatile log/runtime dirs."""
    snap: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in _IGNORED_DIRS for part in rel.parts):
            continue
        snap[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snap


@pytest.fixture
def isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Scratch project root with an empty runs tree and no pins."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    from trw_mcp.models.config import _reset_config, get_config

    _reset_config()
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs

    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    config = get_config()
    (tmp_path / config.runs_root).mkdir(parents=True, exist_ok=True)
    (tmp_path / config.trw_dir).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _ctx(session_id: str) -> Any:
    from trw_mcp.state._paths import TRWCallContext

    return TRWCallContext(
        session_id=session_id,
        client_hint=None,
        explicit=False,
        fastmcp_session=None,
    )


def _seed_run(project_root: Path, task: str, run_id: str) -> Path:
    from trw_mcp.models.config import get_config

    run_dir = project_root / get_config().runs_root / task / run_id
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(
        f"run_id: {run_id}\ntask: {task}\nstatus: active\nphase: implement\n",
        encoding="utf-8",
    )
    return run_dir


# ---------------------------------------------------------------------------
# FR02 — the softened branch
# ---------------------------------------------------------------------------


def test_no_pin_returns_not_recorded_instead_of_raising(isolated_project: Path) -> None:
    """FR02: the no-run branch returns a result dict; no exception propagates."""
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    result = execute_checkpoint(None, "progress", None, None, context=_ctx("fr02-no-pin"))

    assert result["recorded"] is False
    assert "run_path=" in str(result["remedy"])
    assert result["reason"] == "no_active_run"


def test_not_recorded_never_reports_success(isolated_project: Path) -> None:
    """NFR04: no success token anywhere in the payload, and the flag is explicit."""
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    result = execute_checkpoint(None, "progress", None, None, context=_ctx("fr02-truthful"))

    assert "recorded" in result, "the not-recorded marker must be explicit, not inferred"
    assert result["recorded"] is False
    assert result["status"] != "checkpoint_created"
    assert "checkpoint_created" not in json.dumps(result)


def test_no_run_call_writes_nothing_under_the_project_root(isolated_project: Path) -> None:
    """FR02/NFR02: the softened path performs ZERO filesystem writes."""
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    before = _snapshot(isolated_project)

    execute_checkpoint(None, "progress", None, None, context=_ctx("fr02-nowrite"))

    after = _snapshot(isolated_project)
    assert after == before, f"softened path wrote: {set(after) ^ set(before) or 'modified files'}"
    assert not list(isolated_project.rglob("checkpoints.jsonl"))


def test_success_path_marks_recorded_true(isolated_project: Path) -> None:
    """The recorded flag is symmetric — a persisted checkpoint says so explicitly."""
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    run_dir = _seed_run(isolated_project, "task-a", "20260101T000000Z-aaaa1111")

    result = execute_checkpoint(str(run_dir), "did a thing", None, None, context=_ctx("fr02-ok"))

    assert result["recorded"] is True
    assert result["status"] == "checkpoint_created"


def test_supplied_run_path_records_into_that_run(isolated_project: Path) -> None:
    """FR01 mechanism: an explicit in-project run_path wins over the missing pin."""
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    run_dir = _seed_run(isolated_project, "task-a", "20260101T000000Z-aaaa1111")

    execute_checkpoint(str(run_dir), "delegated progress", None, None, context=_ctx("fr02-supplied"))

    checkpoints = run_dir / "meta" / "checkpoints.jsonl"
    events = run_dir / "meta" / "events.jsonl"
    assert checkpoints.exists()
    assert json.loads(checkpoints.read_text(encoding="utf-8").splitlines()[0])["message"] == "delegated progress"
    assert any(json.loads(line)["event"] == "checkpoint" for line in events.read_text(encoding="utf-8").splitlines())


def test_pinned_run_receives_the_checkpoint_without_run_path(isolated_project: Path) -> None:
    """FR01 inheritance: with a pin and no run_path, the pinned run is used."""
    from trw_mcp.state._paths import pin_active_run
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    run_dir = _seed_run(isolated_project, "task-a", "20260101T000000Z-aaaa1111")
    ctx = _ctx("fr02-pinned")
    pin_active_run(run_dir, context=ctx)

    result = execute_checkpoint(None, "inherited progress", None, None, context=ctx)

    assert result["recorded"] is True
    assert (run_dir / "meta" / "checkpoints.jsonl").exists()


# ---------------------------------------------------------------------------
# FR02 boundary — what must still raise
# ---------------------------------------------------------------------------


def test_missing_supplied_run_path_still_raises(isolated_project: Path) -> None:
    """FR02 boundary: a caller mistake is not a missing precondition."""
    from trw_mcp.exceptions import StateError
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    with pytest.raises(StateError):
        execute_checkpoint(
            str(isolated_project / "nope" / "missing-run"),
            "progress",
            None,
            None,
            context=_ctx("fr02-missing"),
        )


def test_non_ctx_caller_still_uses_the_mtime_fallback(isolated_project: Path) -> None:
    """FR02 boundary: legacy (context=None) callers are unchanged."""
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    run_dir = _seed_run(isolated_project, "task-a", "20260101T000000Z-aaaa1111")

    result = execute_checkpoint(None, "legacy progress", None, None, context=None)

    assert result["recorded"] is True
    assert result["status"] == "checkpoint_created"
    assert (run_dir / "meta" / "checkpoints.jsonl").exists()


# ---------------------------------------------------------------------------
# NFR04 at the tool layer — ceremony progress must not lie either
# ---------------------------------------------------------------------------


def test_tool_layer_no_run_call_does_not_advance_ceremony_checkpoint_count(
    isolated_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    orch_tools: dict[str, Any],
) -> None:
    """NFR04: a not-recorded checkpoint must not increment ceremony progress.

    ``mark_checkpoint`` feeds nudges and the deliver gate. Counting a checkpoint
    that was never written would make the framework's own progress record false.
    """
    from trw_mcp.state._ceremony_progress_state import read_ceremony_state
    from trw_mcp.state._paths import resolve_trw_dir

    monkeypatch.setenv("TRW_SESSION_ID", "fr02-tool-layer-unpinned")
    trw_dir = resolve_trw_dir()
    before = read_ceremony_state(trw_dir).checkpoint_count

    result = orch_tools["trw_checkpoint"].fn(
        ctx=SimpleNamespace(session_id="fr02-tool-layer-unpinned"),
        message="progress that was never recorded",
    )

    assert result["recorded"] is False
    assert read_ceremony_state(trw_dir).checkpoint_count == before


def test_tool_layer_recorded_call_does_advance_ceremony_checkpoint_count(
    isolated_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    orch_tools: dict[str, Any],
) -> None:
    """Non-vacuity for the test above: a real checkpoint DOES advance the count."""
    from trw_mcp.state._ceremony_progress_state import read_ceremony_state
    from trw_mcp.state._paths import resolve_trw_dir

    monkeypatch.setenv("TRW_SESSION_ID", "fr02-tool-layer-supplied")
    run_dir = _seed_run(isolated_project, "task-a", "20260101T000000Z-aaaa1111")
    trw_dir = resolve_trw_dir()
    before = read_ceremony_state(trw_dir).checkpoint_count

    result = orch_tools["trw_checkpoint"].fn(
        ctx=SimpleNamespace(session_id="fr02-tool-layer-supplied"),
        run_path=str(run_dir),
        message="real progress",
    )

    assert result["recorded"] is True
    assert read_ceremony_state(trw_dir).checkpoint_count == before + 1


# ---------------------------------------------------------------------------
# Empty message — the same truthfulness contract, applied to the OTHER
# precondition. ``message`` is the whole payload of a checkpoint, so a blank one
# preserves nothing (VISION Principle 5) and must not report that it did (HB-1).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_message_returns_not_recorded_with_a_remedy(isolated_project: Path, blank: str) -> None:
    """A whitespace-only message is as empty as no message at all."""
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    run_dir = _seed_run(isolated_project, "task-a", "20260101T000000Z-aaaa1111")

    result = execute_checkpoint(str(run_dir), blank, None, None, context=_ctx("empty-msg"))

    assert result["recorded"] is False
    assert result["reason"] == "empty_message"
    assert result["status"] != "checkpoint_created"
    assert "checkpoint_created" not in json.dumps(result)
    assert "message=" in str(result["remedy"])


def test_blank_message_writes_nothing_even_with_a_valid_run(isolated_project: Path) -> None:
    """The refusal precedes every write — the run dir is left byte-identical.

    Non-vacuous by construction: the same call with a real message DOES create
    ``checkpoints.jsonl`` (``test_supplied_run_path_records_into_that_run``).
    """
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    run_dir = _seed_run(isolated_project, "task-a", "20260101T000000Z-aaaa1111")
    before = _snapshot(isolated_project)

    execute_checkpoint(str(run_dir), "  ", None, None, context=_ctx("empty-msg-nowrite"))

    assert _snapshot(isolated_project) == before
    assert not (run_dir / "meta" / "checkpoints.jsonl").exists()


def test_blank_message_refused_before_run_resolution_so_a_bad_path_cannot_raise(
    isolated_project: Path,
) -> None:
    """Ordering claim: the message check runs BEFORE resolve_run_path.

    ``test_missing_supplied_run_path_still_raises`` proves this same run_path
    raises when a message is present, so this asserts the ordering rather than
    an accident of the resolver.
    """
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    result = execute_checkpoint(
        str(isolated_project / "nope" / "missing-run"),
        "",
        None,
        None,
        context=_ctx("empty-msg-order"),
    )

    assert result["reason"] == "empty_message"


def test_tool_layer_blank_message_does_not_advance_ceremony_checkpoint_count(
    isolated_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    orch_tools: dict[str, Any],
) -> None:
    """NFR04 for the empty-message branch: the deliver gate's counter must not move."""
    from trw_mcp.state._ceremony_progress_state import read_ceremony_state
    from trw_mcp.state._paths import resolve_trw_dir

    monkeypatch.setenv("TRW_SESSION_ID", "empty-msg-tool-layer")
    run_dir = _seed_run(isolated_project, "task-a", "20260101T000000Z-aaaa1111")
    trw_dir = resolve_trw_dir()
    before = read_ceremony_state(trw_dir).checkpoint_count

    result = orch_tools["trw_checkpoint"].fn(
        ctx=SimpleNamespace(session_id="empty-msg-tool-layer"),
        run_path=str(run_dir),
    )

    assert result["recorded"] is False
    assert read_ceremony_state(trw_dir).checkpoint_count == before
