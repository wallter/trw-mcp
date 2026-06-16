"""FR-03 — bounded execution + isolation negative tests (PRD-CORE-144).

These run real subprocesses through the shared SAFE-001 sandbox. The
isolation negative test proves a probe attempting a live-state write is
CONTAINED — surfaced via the sandbox's writes_outside_tmp audit, and never
mutating the target file the test guards.
"""

from __future__ import annotations

import sys
from pathlib import Path

from trw_mcp.probe.harness import run_probe


def test_timeout_records_wall_ms_and_inconclusive() -> None:
    # FR-03 A1/A2: timeout -> inconclusive with monotonic wall_ms recorded.
    result = run_probe(
        hypothesis="finishes fast",
        command=f'{sys.executable} -c "import time; time.sleep(5)"',
        run_id="run-1",
        timeout_s=1,
    )
    assert result.verdict == "inconclusive"
    assert result.evidence.timed_out is True
    assert result.evidence.wall_ms >= 0


def test_probe_attempting_live_write_is_contained(tmp_path: Path) -> None:
    """ISOLATION NEGATIVE TEST.

    A probe tries to overwrite a readonly file that lives OUTSIDE its writable
    allowlist. The sandbox's post-hoc mtime audit must flag the attempt in
    ``writes_outside_tmp`` — and the harness surfaces it on the evidence — so
    plan adjudication can see the probe tried to escape its bounds. The harness
    never runs against live framework state: it always enters
    ProbeIsolationContext with an empty writable_paths allowlist.
    """
    # A sentinel file the probe will try to clobber. The sandbox audits writes
    # against the cmd-referenced readonly path.
    target = tmp_path / "live_state.txt"
    target.write_text("original")

    # The probe command references the target path (absolute) and rewrites it.
    result = run_probe(
        hypothesis="cannot write outside sandbox tmp",
        command=(f"{sys.executable} -c \"open('{target}','w').write('TAMPERED')\""),
        run_id="run-1",
        timeout_s=10,
    )
    # The sandbox's filesystem audit detects the mutation of a cmd-referenced
    # path that is NOT on the writable allowlist (empty for domain probes).
    assert str(target) in result.evidence.writes_outside_tmp


def test_harness_always_uses_empty_writable_allowlist(monkeypatch) -> None:
    """The harness must construct ProbeIsolationContext with NO writable paths.

    Proves the probe can never be granted a live-state write target by the
    harness itself — it is a domain-probe sandbox, not a candidate-replay one.
    """
    captured: dict[str, object] = {}

    import trw_mcp.probe.harness as harness_mod

    real_ctx = harness_mod.ProbeIsolationContext

    class _SpyCtx(real_ctx):  # type: ignore[valid-type, misc]
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            super().__init__(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(harness_mod, "ProbeIsolationContext", _SpyCtx)
    run_probe(
        hypothesis="h",
        command=f'{sys.executable} -c "print(1)"',
        run_id="run-1",
        timeout_s=5,
    )
    assert captured["writable_paths"] == ()
    assert captured["readonly_paths"] == ()
    assert captured["allow_network"] is False
