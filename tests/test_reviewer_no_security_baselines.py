"""CODEX-P0-B-REVIEWER-WRITES: a reviewer session leaves the shared ``.trw/security`` state untouched.

The MCP security anomaly detector keeps two files under ``.trw/security``: the shadow-period clock
(``mcp_shadow_start.yaml``) and the per-(server, tool) argument-hash baseline (``mcp_arg_baseline.jsonl``).
A stateless reviewer (``TRW_SURFACE_ROLE=reviewer``) must not create or append them in the repository
it reviews. Denial EVENTS are still logged; only these baseline writers are silenced.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


def _detector(tmp_path: Path, *, persist_state: bool):  # type: ignore[no-untyped-def]
    from trw_mcp.security.anomaly_detector import AnomalyDetector, AnomalyDetectorConfig

    config = AnomalyDetectorConfig(
        shadow_clock_path=tmp_path / "security" / "mcp_shadow_start.yaml",
        baseline_store_path=tmp_path / "security" / "mcp_arg_baseline.jsonl",
        persist_state=persist_state,
    )
    return AnomalyDetector(config=config, run_dir=None, fallback_dir=tmp_path / "audit")


def _obs():  # type: ignore[no-untyped-def]
    from trw_mcp.security.anomaly_detector import AnomalyObservation, hash_tool_args

    return AnomalyObservation(
        ts=datetime.now(timezone.utc), server="trw", tool="trw_recall", args_hash=hash_tool_args({"query": "x"})
    )


def test_a_non_persisting_detector_writes_no_security_state(tmp_path: Path) -> None:
    detector = _detector(tmp_path, persist_state=False)
    for _ in range(3):
        detector.observe(_obs())
    assert not (tmp_path / "security").exists()


def test_a_persisting_detector_still_writes_both_files(tmp_path: Path) -> None:
    """The control: without it, a detector that never wrote anything would pass the test above."""
    detector = _detector(tmp_path, persist_state=True)
    detector.observe(_obs())
    assert (tmp_path / "security" / "mcp_shadow_start.yaml").is_file()
    assert (tmp_path / "security" / "mcp_arg_baseline.jsonl").is_file()


def test_a_real_reviewer_server_leaves_security_state_untouched(tmp_path: Path) -> None:
    """End to end through a real ``python -m trw_mcp.server`` child marked reviewer."""
    from tests._stdio_benchmark_support import build_temp_project
    from tests._stdio_harness import stdio_import_skip_reason
    from tests.test_reviewer_posture_stdio_child import _RoleHarness, _rpc

    reason = stdio_import_skip_reason()
    if reason is not None:  # pragma: no cover - environment guard
        pytest.skip(reason)  # skip-category: optional-dependency
    root, user_dir = build_temp_project(tmp_path)
    harness = _RoleHarness(root, user_dir, tmp_path / "stderr", "reviewer")
    try:
        server, _ = harness.cold_initialize("reviewer")
        _rpc(harness, server, "tools/call", {"name": "trw_recall", "arguments": {"query": "*"}})
        _rpc(harness, server, "tools/call", {"name": "trw_learn", "arguments": {"summary": "x", "detail": "y"}})
    finally:
        harness.teardown()
    security = root / ".trw" / "security"
    assert not (security / "mcp_shadow_start.yaml").exists()
    assert not (security / "mcp_arg_baseline.jsonl").exists()
