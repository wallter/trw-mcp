"""Real sync measurements; only the explicit test baseline bypasses root guards."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._tools_learning_shared import instructions_sync_fn
from trw_mcp.models.config import reload_config
from trw_mcp.state import _paths
from trw_mcp.state.claude_md import _write_guard
from trw_mcp.state.persistence import FileStateWriter

_TARGETS = ("AGENTS.md",)
_BODY = "\n".join(f"# line {i}" for i in range(2000)) + "\n"


@dataclass
class SyncMeasurement:
    target_ms: dict[str, float]
    contents: dict[str, str]
    cold_sync_ms: float


def measure_sync(
    root: Path,
    *,
    bypass_guard: bool,
    clock: Callable[[], float] = time.perf_counter,
) -> SyncMeasurement:
    """Measure each root write inside actual sync, with identical cold config setup."""
    (root / ".trw").mkdir(parents=True)
    (root / ".trw/config.yaml").write_text(
        "max_auto_lines: 3000\nagents_md_enabled: true\n",
        encoding="utf-8",
    )
    for name in _TARGETS:
        (root / name).write_text(_BODY, encoding="utf-8")
    targets = {root / name for name in _TARGETS}
    timings: dict[str, float] = {}
    original_guard = _write_guard.guarded_instruction_write

    def measured_write(target: Path, candidate: str, **kwargs: object) -> _write_guard.InstructionWriteVerdict:
        if target not in targets:
            return original_guard(target, candidate, **kwargs)
        assert target.name not in timings, f"unexpected repeated root write: {target}"
        start = clock()
        if bypass_guard:
            # Test-only counterfactual: retain the same durable atomic writer.
            FileStateWriter().write_text(target, candidate)
            verdict = _write_guard.InstructionWriteVerdict(written=True, total_lines=len(candidate.split("\n")))
        else:
            # Preserve every real argument.
            verdict = original_guard(target, candidate, **kwargs)
        timings[target.name] = (clock() - start) * 1000
        assert verdict.written, verdict
        if not bypass_guard:
            assert verdict.backup_path is not None
            assert Path(verdict.backup_path).read_text(encoding="utf-8") == _BODY
        return verdict

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TRW_PROJECT_ROOT", str(root))
        patch.setattr(_paths, "resolve_project_root", lambda *a, **k: root)
        patch.setattr(_paths, "resolve_trw_dir", lambda *a, **k: root / ".trw")
        patch.setattr(_write_guard, "guarded_instruction_write", measured_write)
        # Registration may consult config; neither arm inherits that warm state.
        reload_config(None)
        start = time.perf_counter()
        try:
            result = instructions_sync_fn(client="all")
        finally:
            cold_sync_ms = (time.perf_counter() - start) * 1000
            reload_config(None)
    assert result["status"] == "synced", result
    assert set(timings) == set(_TARGETS), "every root file must reach the measured write seam"
    contents = {name: (root / name).read_text(encoding="utf-8") for name in _TARGETS}
    for content in contents.values():
        assert content.startswith(_BODY), "sync lost or changed the original user lines"
        assert content != _BODY, "sync must write a generated section, not skip or rewrite identical bytes"
    return SyncMeasurement(timings, contents, cold_sync_ms)
