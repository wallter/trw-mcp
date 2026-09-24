"""N1: run.yaml read-modify-write goes through one locked path.

The concurrency proof uses real processes, because the lost-update race the lock
closes is between separate MCP server processes writing the same run.
"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

import pytest

from trw_mcp.formation._admission import revoke_run_stamp
from trw_mcp.state._run_yaml_update import update_run_yaml
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

pytestmark = pytest.mark.unit

_ROUNDS = 40


def _new_run(tmp_path: Path, data: dict[str, object]) -> Path:
    run = tmp_path / "run"
    FileStateWriter().write_yaml(run / "meta" / "run.yaml", data)
    return run


def _read(run: Path) -> dict[str, object]:
    return FileStateReader().read_yaml(run / "meta" / "run.yaml")


def _bump(run: str, key: str) -> None:
    def _inc(data: dict[str, object]) -> None:
        current = data.get(key, 0)
        assert isinstance(current, int)
        data[key] = current + 1

    for _ in range(_ROUNDS):
        update_run_yaml(Path(run), _inc)


@pytest.mark.skipif(sys.platform == "win32", reason="advisory flock is a no-op on Windows")
def test_concurrent_writers_lose_no_update(tmp_path: Path) -> None:
    run = _new_run(tmp_path, {"run_id": "r1", "phase": "implement"})
    ctx = multiprocessing.get_context("spawn")
    keys = ["phase_writes", "stamp_writes", "metrics_writes"]
    procs = [ctx.Process(target=_bump, args=(str(run), key)) for key in keys]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=120)
        assert proc.exitcode == 0
    data = _read(run)
    assert {key: data[key] for key in keys} == dict.fromkeys(keys, _ROUNDS)
    assert data["run_id"] == "r1"


def test_missing_run_is_not_created(tmp_path: Path) -> None:
    run = tmp_path / "absent"
    assert update_run_yaml(run, lambda data: data.update(status="complete")) is False
    assert not run.exists()


def test_meta_without_run_yaml_is_not_created(tmp_path: Path) -> None:
    (tmp_path / "run" / "meta").mkdir(parents=True)
    assert update_run_yaml(tmp_path / "run", lambda data: data.update(status="complete")) is False
    assert not (tmp_path / "run" / "meta" / "run.yaml").exists()


def test_noop_mutation_does_not_rewrite(tmp_path: Path) -> None:
    run = _new_run(tmp_path, {"run_id": "r1", "status": "active"})
    target = run / "meta" / "run.yaml"
    before = target.stat().st_mtime_ns
    assert update_run_yaml(run, lambda data: data.update(status="active")) is True
    assert target.stat().st_mtime_ns == before


def test_revoke_keeps_a_concurrent_writers_key(tmp_path: Path) -> None:
    run = _new_run(tmp_path, {"run_id": "r1", "formation_id": "f1", "member_id": "m1"})
    update_run_yaml(run, lambda data: data.update(phase="validate"))
    assert revoke_run_stamp(run) is True
    data = _read(run)
    assert data["phase"] == "validate"
    assert data["revoked_formation_id"] == "f1"
    assert "formation_id" not in data
    assert revoke_run_stamp(run) is False


def _advance_phases(run: str, project_root: str) -> None:
    """A real phase writer (state/phase.update_run_phase) in its own process."""
    import os

    os.environ["TRW_PROJECT_ROOT"] = project_root  # the ceremony mirror must not touch this repo's .trw
    from trw_mcp.models.run import Phase
    from trw_mcp.state.phase import update_run_phase

    for phase in (Phase.PLAN, Phase.IMPLEMENT, Phase.VALIDATE, Phase.REVIEW, Phase.DELIVER):
        assert update_run_phase(Path(run), phase) is True


@pytest.mark.skipif(sys.platform == "win32", reason="advisory flock is a no-op on Windows")
def test_a_real_phase_writer_and_concurrent_writers_lose_nothing(tmp_path: Path) -> None:
    """W1 half of N1: update_run_phase decides forward-only INSIDE the lock, beside two racing writers."""
    run = _new_run(tmp_path, {"run_id": "r1", "phase": "research", "status": "active"})
    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_advance_phases, args=(str(run), str(tmp_path))),
        ctx.Process(target=_bump, args=(str(run), "stamp_writes")),
        ctx.Process(target=_bump, args=(str(run), "metrics_writes")),
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=120)
        assert proc.exitcode == 0
    data = _read(run)
    assert data["phase"] == "deliver"
    assert (data["stamp_writes"], data["metrics_writes"]) == (_ROUNDS, _ROUNDS)


def test_create_run_yaml_refuses_to_overwrite_a_run(tmp_path: Path) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.state._run_yaml_update import create_run_yaml

    run = tmp_path / "run"
    create_run_yaml(run, {"run_id": "r1", "phase": "research"})
    assert _read(run)["run_id"] == "r1"
    with pytest.raises(StateError, match="already exists"):
        create_run_yaml(run, {"run_id": "r2"})
    assert _read(run)["run_id"] == "r1", "a second init must never replace a live run's state"


def test_phase_update_is_forward_only_and_writes_nothing_backwards(tmp_path: Path) -> None:
    from trw_mcp.models.run import Phase
    from trw_mcp.state.phase import update_run_phase

    run = _new_run(tmp_path, {"run_id": "r1", "phase": "validate"})
    target = run / "meta" / "run.yaml"
    before = target.stat().st_mtime_ns
    assert update_run_phase(run, Phase.PLAN) is False
    assert _read(run)["phase"] == "validate" and target.stat().st_mtime_ns == before


def test_complete_run_yaml_replaces_only_this_inits_scaffold(tmp_path: Path) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.state._run_yaml_update import complete_run_yaml, create_run_yaml

    run = tmp_path / "run"
    with pytest.raises(StateError, match="not this init's scaffold"):
        complete_run_yaml(run, {"run_id": "r1", "phase": "research", "objective": "x"})  # nothing scaffolded
    create_run_yaml(run, {"run_id": "r1", "phase": "research"})
    complete_run_yaml(run, {"run_id": "r1", "phase": "research", "objective": "x"})
    assert _read(run)["objective"] == "x"
    with pytest.raises(StateError, match="not this init's scaffold"):
        complete_run_yaml(run, {"run_id": "r2", "phase": "research"})
    assert _read(run)["run_id"] == "r1"


def test_phase_gate_runs_outside_the_lock_and_a_raced_phase_is_rejudged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exit gate may shell out for seconds, so it runs unlocked (C review SF1).

    The gate here WRITES run.yaml itself, as a concurrent writer would: under the old
    shape (gate inside the lock) that write would block on the same lock forever. The
    phase it moved to makes the first judgement stale, so nothing is written for it;
    the second attempt judges the new phase and advances.
    """
    from trw_mcp.models.run import Phase
    from trw_mcp.state import phase as phase_mod

    run = _new_run(tmp_path, {"run_id": "r1", "phase": "research"})
    judged: list[str] = []

    def _gate(run_path: Path, current: str, _data: dict[str, object]) -> None:
        judged.append(current)
        if len(judged) == 1:
            update_run_yaml(run_path, lambda d: d.update(phase="plan"))

    monkeypatch.setattr(phase_mod, "_enforce_exit_gate", _gate)
    monkeypatch.setattr(phase_mod, "_mirror_ceremony_phase", lambda _p: None)
    assert phase_mod.update_run_phase(run, Phase.IMPLEMENT) is True
    assert judged == ["research", "plan"]
    assert _read(run)["phase"] == "implement"


def test_phase_update_gives_up_after_two_raced_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.models.run import Phase
    from trw_mcp.state import phase as phase_mod

    run = _new_run(tmp_path, {"run_id": "r1", "phase": "research"})
    moves = iter(["plan", "research"])
    monkeypatch.setattr(
        phase_mod, "_enforce_exit_gate", lambda p, _c, _d: update_run_yaml(p, lambda d: d.update(phase=next(moves)))
    )
    assert phase_mod.update_run_phase(run, Phase.IMPLEMENT) is False
    assert _read(run)["phase"] == "research"  # the racing writer's value, never overwritten
