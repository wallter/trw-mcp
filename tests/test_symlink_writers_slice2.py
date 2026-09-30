"""SYMLINK-WRITERS slice 2 (audit/SYMLINK-WRITERS-CENSUS.md): a planted leaf symlink is refused, never written through.

Each site wrote with ``Path.write_text`` / ``open("w")``, which follow a symlink at the leaf: a checkout or
run directory carrying ``compact_instructions.txt -> ~/.zshrc`` got repo-derived text written into the
user's shell startup file. The victim here stands in for that file and must come out byte-identical.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_VICTIM_TEXT = "export PATH=/usr/bin\n"


def _victim(tmp_path: Path) -> Path:
    victim = tmp_path / "outside" / "zshrc"
    victim.parent.mkdir(parents=True)
    victim.write_text(_VICTIM_TEXT, encoding="utf-8")
    return victim


def _plant(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)


def test_compact_instructions_refuse_a_planted_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp._checkout_write import UnsafeWriteError
    from trw_mcp.tools import checkpoint

    victim = _victim(tmp_path)
    project = tmp_path / "project"
    run_dir = project / ".trw" / "runs" / "t" / "r1"
    (run_dir / "meta").mkdir(parents=True)
    _plant(project / ".trw" / "context" / "compact_instructions.txt", victim)
    monkeypatch.setattr(checkpoint, "_compute_pending_ceremony", lambda *_a, **_k: [])
    cfg = SimpleNamespace(
        compact_instructions_template="phase={phase} {run_id} {prd_scope} {last_checkpoint} "
        "{formation} {failing_tests} {ceremony_pending}"
    )

    with pytest.raises(UnsafeWriteError):
        checkpoint._write_compact_instructions(cfg, project, run_dir, "implement", [], "solo", ["t::x"], {})
    assert victim.read_text(encoding="utf-8") == _VICTIM_TEXT


def test_compact_instructions_still_write_a_regular_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools import checkpoint

    project = tmp_path / "project"
    run_dir = project / ".trw" / "runs" / "t" / "r1"
    (run_dir / "meta").mkdir(parents=True)
    monkeypatch.setattr(checkpoint, "_compute_pending_ceremony", lambda *_a, **_k: [])
    cfg = SimpleNamespace(
        compact_instructions_template="phase={phase} {run_id} {prd_scope} {last_checkpoint} "
        "{formation} {failing_tests} {ceremony_pending}"
    )

    path = checkpoint._write_compact_instructions(cfg, project, run_dir, "implement", [], "solo", [], {})
    assert path.read_text(encoding="utf-8").startswith("phase=implement r1 none")


def _stub_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    outcome = SimpleNamespace(accepted_blocked=[], unmet=[], enumerated=[])
    monkeypatch.setattr("trw_mcp.tools._plan_acceptance_gate.evaluate_plan_acceptance", lambda *_a: outcome)
    monkeypatch.setattr(
        "trw_mcp.tools._project_handoff.write_handoff_rows", lambda **_k: {"status": "ok", "path": "HANDOFF.md"}
    )


def test_project_handoff_refuses_a_planted_final_md_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools._ceremony_deliver_steps import step_project_handoff

    victim = _victim(tmp_path)
    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    _plant(run / "reports" / "final.md", victim)
    _stub_handoff(monkeypatch)
    results: dict[str, Any] = {}

    step_project_handoff(run, results)  # type: ignore[arg-type]

    assert victim.read_text(encoding="utf-8") == _VICTIM_TEXT
    assert results["project_handoff"]["status"] == "failed"  # fail-open, and never absent-meaning-succeeded


def test_project_handoff_still_writes_a_regular_final_md(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools._ceremony_deliver_steps import step_project_handoff

    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    (run / "reports").mkdir()
    (run / "reports" / "final.md").write_text("# Final\n\nmine\n", encoding="utf-8")
    _stub_handoff(monkeypatch)
    results: dict[str, Any] = {}

    step_project_handoff(run, results)  # type: ignore[arg-type]

    text = (run / "reports" / "final.md").read_text(encoding="utf-8")
    assert text.startswith("# Final\n\nmine\n") and results["project_handoff"]["status"] == "ok"


def test_lock_for_rmw_refuses_a_planted_lock_symlink(tmp_path: Path) -> None:
    from trw_mcp.state._persistence_helpers import lock_for_rmw

    victim = _victim(tmp_path)
    state = tmp_path / "project" / ".trw" / "learnings" / "index.yaml"
    _plant(state.parent / "index.yaml.lock", victim)

    with pytest.raises(OSError), lock_for_rmw(state):
        pass
    assert victim.read_text(encoding="utf-8") == _VICTIM_TEXT


def test_lock_for_rmw_locks_a_regular_file(tmp_path: Path) -> None:
    from trw_mcp.state._persistence_helpers import lock_for_rmw

    state = tmp_path / "project" / ".trw" / "run.yaml"
    with lock_for_rmw(state) as locked:
        assert locked == state
    assert (state.parent / "run.yaml.lock").is_file()
