"""A stuck memory daemon cannot hang an install (PRD-CORE-305-FR07, sol final round).

The CLAUDE.md sync now runs inside update-project's transaction, in the caller's
thread. It reaches REVIEW.md generation, which recalls learnings through the
loopback daemon, and ``_daemon_store._run`` used to wait on the daemon future with
no deadline: a slow or stuck daemon hung the whole update. Inside an install every
daemon call now shares one bounded budget, and running out degrades exactly like an
unreachable daemon (``StoreUnavailableError``: the recall returns nothing).
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.bootstrap import _ide_targets_finalize, _update_project
from trw_mcp.state import _daemon_store, _store_selection
from trw_mcp.state._project_root_binding import installing_into
from trw_mcp.state._store_selection import StoreUnavailableError
from trw_mcp.state.claude_md import _sync

_BUDGET_S = 0.5
_HANG_GUARD_S = 20.0


class _StuckDaemonClient:
    """Every daemon request is accepted and never answered (until the test releases it)."""

    def __init__(self, release: threading.Event) -> None:
        self._release = release
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        async def _never_answers(*_args: object, **_kwargs: object) -> dict[str, object]:
            self.calls.append(name)
            while not self._release.is_set():  # noqa: ASYNC110 -- a threading.Event the test thread sets
                await asyncio.sleep(0.02)
            return {"status": "ok", "entries": [], "next": None}

        return _never_answers


@pytest.fixture
def stuck_daemon(monkeypatch: pytest.MonkeyPatch) -> Any:
    release = threading.Event()
    client = _StuckDaemonClient(release)
    store = _daemon_store.DaemonMemoryStore(client, "project:stuck-00000000")  # type: ignore[arg-type]
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, "project:stuck-00000000"))
    monkeypatch.setattr(_daemon_store, "INSTALL_DAEMON_BUDGET_S", _BUDGET_S, raising=False)
    yield client
    release.set()


def _run_guarded(fn: Any) -> float:
    """Run *fn* on a thread; fail (rather than hang the suite) if it outlives the guard."""
    outcome: dict[str, BaseException] = {}

    def _target() -> None:
        try:
            fn()
        except BaseException as exc:  # surfaced below
            outcome["exc"] = exc

    started = time.monotonic()
    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout=_HANG_GUARD_S)
    elapsed = time.monotonic() - started
    assert not worker.is_alive(), f"the install was still waiting on the stuck daemon after {_HANG_GUARD_S}s"
    if "exc" in outcome:
        raise outcome["exc"]
    return elapsed


def test_a_stuck_daemon_does_not_hang_the_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stuck_daemon: Any
) -> None:
    target = tmp_path / "project"
    (target / ".trw").mkdir(parents=True)
    result: dict[str, list[str]] = {"errors": [], "warnings": [], "preserved": [], "updated": [], "created": []}

    def _writer_phase(root: Path, _data: Path, res: dict[str, list[str]], *_a: object, **_k: object) -> None:
        _sync.generate_review_md(root / ".trw", repo_root=root)  # the six serial tag recalls
        _ide_targets_finalize._run_claude_md_sync(root, res)

    monkeypatch.setattr(_update_project, "_run_core_update_phases", _writer_phase)
    monkeypatch.setattr(_update_project, "_run_post_update_phases", lambda *_a, **_k: None)

    elapsed = _run_guarded(
        lambda: _update_project._apply_update(
            target, target, result, ide=None, on_progress=None, dirty=None, reprovision=None
        )
    )

    assert stuck_daemon.calls, "the fake daemon was never asked: the test proves nothing"
    assert elapsed < _BUDGET_S + 5.0, f"the update waited {elapsed:.1f}s on a stuck daemon (budget {_BUDGET_S}s)"
    assert result["errors"] == []
    assert "No qualifying learnings" in (target / "REVIEW.md").read_text(encoding="utf-8")


def test_the_budget_is_shared_by_every_call_in_one_install(tmp_path: Path, stuck_daemon: Any) -> None:
    """The first call spends the budget; the rest fail fast instead of each waiting it out."""
    target = tmp_path / "project"
    target.mkdir()
    waits: list[float] = []

    def _three_calls() -> None:
        with installing_into(target):
            for _ in range(3):
                started = time.monotonic()
                with pytest.raises(StoreUnavailableError, match="budget"):
                    _daemon_store._run(stuck_daemon.status("project:stuck-00000000"))
                waits.append(time.monotonic() - started)

    _run_guarded(_three_calls)

    assert waits[0] >= _BUDGET_S * 0.8
    assert sum(waits[1:]) < 0.5, f"later calls waited again: {waits}"


def test_outside_an_install_the_wait_is_unbounded_as_before(stuck_daemon: Any) -> None:
    """A server request is not an install: its daemon waits keep their existing (unbounded) semantics."""
    loop, _thread = _daemon_store._daemon_loop()
    fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop)
    fut.result(timeout=5)
    answered: dict[str, Any] = {}

    def _call() -> None:
        answered["value"] = _daemon_store._run(stuck_daemon.status("project:stuck-00000000"))

    worker = threading.Thread(target=_call, daemon=True)
    worker.start()
    worker.join(timeout=_BUDGET_S * 3)
    assert worker.is_alive(), "a call outside an install was cut off by the install budget"
    stuck_daemon._release.set()
    worker.join(timeout=5)
    assert answered["value"]["status"] == "ok"


def test_auto_maintenance_draws_on_the_updates_budget_not_a_fresh_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stuck_daemon: Any
) -> None:
    """Queued row (a): auto-maintenance ran after the writer phase's binding closed and opened its own budget,
    so a stalled daemon cost an update two budgets. One binding now spans the whole update."""
    target = tmp_path / "project"
    (target / ".trw").mkdir(parents=True)

    def _writer_phase_spends_the_budget(root: Path, *_a: object, **_k: object) -> None:
        with installing_into(root), pytest.raises(StoreUnavailableError, match="budget"):
            _daemon_store._run(stuck_daemon.status("project:stuck-00000000"))

    from trw_mcp.bootstrap import _rerender

    # 9.2.2 moved the not-a-repo / no-.trw / manifest-refusal pre-flight into _rerender.validate_update_target.
    monkeypatch.setattr(_rerender, "validate_update_target", lambda *_a: True)
    for name, stub in {
        "dirty_state": lambda *_a: (None, []),
        "_apply_update": _writer_phase_spends_the_budget,
        "_update_git_hooks": lambda *_a: None,
        "pin_empty_checkout": lambda *_a: None,
        "_cleanup_context_transients": lambda *_a: None,
    }.items():
        monkeypatch.setattr(_update_project, name, stub)
    real_maintenance = _update_project._run_auto_maintenance
    spent: dict[str, float] = {}

    def _timed_maintenance(*args: Any, **kwargs: Any) -> None:
        started = time.monotonic()
        real_maintenance(*args, **kwargs)
        spent["maintenance"] = time.monotonic() - started

    monkeypatch.setattr(_update_project, "_run_auto_maintenance", _timed_maintenance)

    result: dict[str, Any] = {}
    _run_guarded(lambda: result.update(_update_project.update_project(target)))

    assert "maintenance" in spent, f"auto-maintenance never ran: {result.get('errors')}"
    assert spent["maintenance"] < _BUDGET_S * 0.5, (
        f"auto-maintenance waited {spent['maintenance']:.2f}s on the stuck daemon after the update spent its budget"
    )
    assert any("Auto-maintenance skipped" in w or "budget" in w for w in result.get("warnings", [])), result.get(
        "warnings"
    )


def test_a_budget_exhausted_by_a_cold_daemon_is_reported_as_warming_not_as_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stuck_daemon: Any
) -> None:
    """FB-INSTALL-07: right after the installer restarts the daemon it loads its model for tens of seconds; the update
    must say that (and what it skipped), not 'Auto-maintenance skipped: ...' with the exception text."""
    from trw_mcp.bootstrap._update_external import _run_auto_maintenance
    from trw_mcp.models.config import get_config

    monkeypatch.setattr(get_config(), "embeddings_enabled", True)
    target = tmp_path / "project"
    (target / ".trw").mkdir(parents=True)
    result: dict[str, list[str]] = {"warnings": []}

    _run_guarded(lambda: _run_auto_maintenance(target, result))

    [warning] = result["warnings"]
    assert "Auto-maintenance skipped" not in warning
    assert "may still be loading its embedding model" in warning and "trw-mcp doctor" in warning
    assert "budget" in warning
