"""NFR02/NFR03 — bounded runtime and determinism on the real repository."""

from __future__ import annotations

import time
from pathlib import Path

from tests._layout import requires_local_timing
from tests._timing import assert_budget
from trw_mcp.wiring.detector import DetectorResult, run_detector

BUDGET_SECONDS = 10.0
# Bounded retries absorb transient box contention (this repo's dev boxes
# routinely run several concurrent agent sessions) without weakening the
# budget itself -- a genuine regression still fails every attempt. See
# tests/wiring/test_specimen_fixture.py::test_full_scan_under_ten_seconds for
# the confirmed 2026-09-03 contention measurement (11.92s under `-n 8` load,
# passing again once load dropped).
_MAX_SCAN_ATTEMPTS = 3


@requires_local_timing
def test_full_scan_under_ten_seconds(repo_root: Path) -> None:
    """A check people are tempted to disable is a check that gets disabled."""
    best_elapsed = float("inf")
    best_duration = float("inf")
    for _attempt in range(_MAX_SCAN_ATTEMPTS):
        started = time.monotonic()
        result = run_detector(repo_root)
        elapsed = time.monotonic() - started
        best_elapsed = min(best_elapsed, elapsed)
        best_duration = min(best_duration, result.duration_seconds)
        if elapsed < BUDGET_SECONDS and result.duration_seconds < BUDGET_SECONDS:
            break
    assert_budget("repo_wide_scan_wall_time", best_elapsed, BUDGET_SECONDS, "s")
    assert_budget("repo_wide_scan_reported_duration", best_duration, BUDGET_SECONDS, "s")


def test_identical_input_yields_identical_findings(repo_root: Path, live_result: DetectorResult) -> None:
    """NFR03: no network, no clock-dependent behaviour, sorted traversal."""
    second = run_detector(repo_root)
    assert [f.key for f in second.findings] == [f.key for f in live_result.findings]
    assert [f.evidence for f in second.findings] == [f.evidence for f in live_result.findings]


def test_findings_are_sorted(live_result: DetectorResult) -> None:
    keys = [f.key for f in live_result.findings]
    assert keys == sorted(keys)


def test_ip_boundary_preserved() -> None:
    """NFR05: zero ``trw_distill`` imports in the detector. The sidecar is a path convention."""
    package = Path(__file__).resolve().parents[2] / "src/trw_mcp/wiring"
    for source in sorted(package.rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        assert "import trw_distill" not in text, source
        assert "from trw_distill" not in text, source


def test_no_reachability_analysis_was_built() -> None:
    """Phase B was demoted for measured cause: ~1-in-8 recall on this defect class.

    Pinned so a future contributor does not quietly reintroduce a call graph and
    with it the 47% false-positive rate this design exists to avoid.
    """
    package = Path(__file__).resolve().parents[2] / "src/trw_mcp/wiring"
    combined = "\n".join(source.read_text(encoding="utf-8") for source in sorted(package.rglob("*.py")))
    for banned in ("call_graph", "CallGraph", "build_call_graph", "reachable_from"):
        assert banned not in combined, f"reachability construct {banned!r} found in the detector"
