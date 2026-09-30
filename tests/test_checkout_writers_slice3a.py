"""Security slice 3A: the writers W5's census rated CRITICAL/HIGH refuse a planted symlink instead of writing through it.

A hostile branch ships a symlink at a path TRW writes. Before this slice, ``compact_instructions.txt`` and a run's
``reports/final.md`` were written with a plain ``write_text`` that follows the link, putting run-derived text into
whatever file it names (a shell rc file, say). Each test plants the link, runs the real writer, and checks the
outside file kept its bytes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._fs_hazards import assert_user_bytes_preserved, snapshot_user_bytes

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics (Windows needs privileges)"),
]


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.rc").write_text("export SAFE=1\n", encoding="utf-8")
    return outside


def test_compact_instructions_refuse_a_planted_leaf_symlink(tmp_path: Path) -> None:
    from trw_mcp._checkout_write import UnsafeWriteError
    from trw_mcp.tools.checkpoint import _write_compact_instructions

    outside = _outside(tmp_path)
    before = snapshot_user_bytes(outside)
    project = tmp_path / "project"
    (project / ".trw" / "context").mkdir(parents=True)
    os.symlink(outside / "victim.rc", project / ".trw" / "context" / "compact_instructions.txt")
    run_dir = project / ".trw" / "runs" / "t" / "r1"
    (run_dir / "meta").mkdir(parents=True)
    cfg = SimpleNamespace(compact_instructions_template="phase={phase}\n")

    with pytest.raises(UnsafeWriteError):
        _write_compact_instructions(cfg, project, run_dir, "implement", [], "", [], {})

    assert_user_bytes_preserved(before, outside)


def test_compact_instructions_are_written_normally_without_a_link(tmp_path: Path) -> None:
    from trw_mcp.tools.checkpoint import _write_compact_instructions

    project = tmp_path / "project"
    run_dir = project / ".trw" / "runs" / "t" / "r1"
    (run_dir / "meta").mkdir(parents=True)
    cfg = SimpleNamespace(compact_instructions_template="phase={phase}\n")

    path = _write_compact_instructions(cfg, project, run_dir, "implement", [], "", [], {})

    assert path.read_text(encoding="utf-8") == "phase=implement\n"


@pytest.mark.parametrize("planted", ["leaf", "reports-dir"])
def test_deliver_handoff_never_reads_or_writes_through_a_planted_final_md_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, planted: str
) -> None:
    from trw_mcp.tools import _ceremony_deliver_steps as steps
    from trw_mcp.tools import _plan_acceptance_gate, _project_handoff

    outside = _outside(tmp_path)
    before = snapshot_user_bytes(outside)
    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    if planted == "leaf":
        (run / "reports").mkdir()
        os.symlink(outside / "victim.rc", run / "reports" / "final.md")
    else:
        os.symlink(outside, run / "reports")
    monkeypatch.setattr(
        _plan_acceptance_gate,
        "evaluate_plan_acceptance",
        lambda *_a: SimpleNamespace(accepted_blocked=[], unmet=[], enumerated=[]),
    )
    monkeypatch.setattr(_project_handoff, "write_handoff_rows", lambda **_k: {"status": "ok", "path": "HANDOFF.md"})
    results: dict[str, object] = {}

    steps.step_project_handoff(run, results)  # type: ignore[arg-type]

    assert results["project_handoff"]["status"] == "failed"  # type: ignore[index]
    assert_user_bytes_preserved(before, outside)


def test_deliver_handoff_never_reads_final_md_through_a_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """codex known_issue on slice 3A: prove the outside target is not READ, not only not written."""
    from trw_mcp.tools import _ceremony_deliver_steps as steps
    from trw_mcp.tools import _plan_acceptance_gate, _project_handoff

    outside = _outside(tmp_path)
    run = tmp_path / "run"
    (run / "reports").mkdir(parents=True)
    (run / "meta").mkdir()
    os.symlink(outside / "victim.rc", run / "reports" / "final.md")
    monkeypatch.setattr(
        _plan_acceptance_gate,
        "evaluate_plan_acceptance",
        lambda *_a: SimpleNamespace(accepted_blocked=[], unmet=[], enumerated=[]),
    )
    monkeypatch.setattr(_project_handoff, "write_handoff_rows", lambda **_k: {"status": "ok", "path": "HANDOFF.md"})
    reads: list[Path] = []
    real_read_text = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: reads.append(self) or real_read_text(self, *a, **k))

    steps.step_project_handoff(run, {})  # type: ignore[arg-type]

    assert run / "reports" / "final.md" not in reads


def test_telemetry_truncate_refuses_a_linked_log(tmp_path: Path) -> None:
    from trw_mcp.telemetry.pipeline import TelemetryPipeline

    outside = _outside(tmp_path)
    before = snapshot_user_bytes(outside)
    log = tmp_path / "logs" / "pipeline-events.jsonl"
    log.parent.mkdir()
    os.symlink(outside / "victim.rc", log)

    TelemetryPipeline._truncate_jsonl(log)  # fail-open: the refusal is logged, never raised

    assert_user_bytes_preserved(before, outside)
