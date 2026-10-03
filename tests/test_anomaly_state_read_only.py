"""The anomaly detector keeps working when it cannot write its own state (DoD-5 AGY-SANDBOX-WRITE-FAILS).

antigravity-cli runs its MCP server inside a sandbox that denies project writes. The
detector's first observation wrote ``.trw/security/mcp_shadow_start.yaml`` and let the
OSError escape, so ``trw_session_start`` and ``trw_code`` failed on every sandboxed run.
Its shadow clock and arg-hash baseline are now best-effort: a refused write is logged and
detection continues in memory.
"""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest
from structlog.testing import capture_logs

_POSIX_NON_ROOT = hasattr(os, "geteuid") and os.geteuid() != 0


def _detector(state_dir: Path):  # type: ignore[no-untyped-def]
    from trw_mcp.security.anomaly_detector import AnomalyDetector, AnomalyDetectorConfig

    config = AnomalyDetectorConfig(
        checkout_root=state_dir,
        shadow_clock_path=state_dir / "security" / "mcp_shadow_start.yaml",
        baseline_store_path=state_dir / "security" / "mcp_arg_baseline.jsonl",
    )
    return AnomalyDetector(config=config, run_dir=None, fallback_dir=state_dir / "audit")


def _observe(detector, args_hash: str) -> list[str]:  # type: ignore[no-untyped-def]
    from trw_mcp.security.anomaly_detector import AnomalyObservation

    obs = AnomalyObservation(
        ts=datetime.now(tz=timezone.utc), server="trw", tool="trw_session_start", args_hash=args_hash
    )
    fired: list[str] = detector.observe(obs)
    return fired


@pytest.mark.skipif(not _POSIX_NON_ROOT, reason="needs POSIX directory permissions enforced for this user")
def test_observe_succeeds_and_keeps_detecting_when_the_state_dir_is_read_only(tmp_path: Path) -> None:
    state = tmp_path / "trw"
    state.mkdir()
    state.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        detector = _detector(state)
        with capture_logs() as logs:
            first = _observe(detector, "h1")
            repeat = _observe(detector, "h1")
            novel = _observe(detector, "h2")
    finally:
        state.chmod(stat.S_IRWXU)

    # Detection still runs on the in-memory baseline: a repeated shape is known, a new one is novel.
    assert "novel_arg_pattern" in first
    assert "novel_arg_pattern" not in repeat
    assert "novel_arg_pattern" in novel
    assert [e for e in logs if e.get("event") == "mcp_anomaly_state_unwritable"], logs
    assert not (state / "security").exists()


def test_observe_still_writes_state_when_it_can(tmp_path: Path) -> None:
    detector = _detector(tmp_path)

    _observe(detector, "h1")

    assert (tmp_path / "security" / "mcp_shadow_start.yaml").is_file()
    assert (tmp_path / "security" / "mcp_arg_baseline.jsonl").is_file()
