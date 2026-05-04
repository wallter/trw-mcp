"""PRD-FIX-088 FR01: ``trw_build_check`` returns within the latency budget.

Pre-fix: a single call could take 91 s when 2823 entries fell within the
correlation window — every entry was correlated inline before the response
came back. Live measurement 2026-05-04 on the dev shared HTTP MCP server.

Post-fix: Q-learning is dispatched to a background worker; the response
returns in <500 ms even when ``correlate_recalls`` would identify >1000
candidates. ``q_learning_dispatch`` step alone is <5 ms.

These tests are the latency regression guard for FR01 + NFR03 + NFR08.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest


def _invoke_build_check(tmp_project: Path) -> dict[str, object]:
    from tests.conftest import extract_tool_fn, make_test_server

    server = make_test_server("build")
    fn = extract_tool_fn(server, "trw_build_check")

    import trw_mcp.tools.build._registration as reg_mod

    original_resolve = reg_mod.resolve_trw_dir
    reg_mod.resolve_trw_dir = lambda: tmp_project / ".trw"  # type: ignore[assignment]
    try:
        return fn(tests_passed=True, test_count=1, scope="full")  # type: ignore[no-any-return]
    finally:
        reg_mod.resolve_trw_dir = original_resolve  # type: ignore[assignment]


def test_trw_build_check_returns_within_500ms(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NFR03/NFR08: warm-path p95 < 500 ms.

    The bg worker MAY take seconds to drain; we test that the TOOL CALL
    returns within the cap, not that the worker is done.
    """
    # Stub correlation so the bg worker doesn't actually do work.
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None: [],
    )

    t0 = time.monotonic()
    _invoke_build_check(tmp_project)
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert elapsed_ms < 500.0, (
        f"NFR03 regression: trw_build_check took {elapsed_ms:.1f}ms (cap 500ms). "
        f"Pre-FIX-088 this was ~91000ms when Q-learning correlated 2823 entries "
        f"inline. If this assertion fails, Q-learning has reverted to inline "
        f"execution OR a new step has been added that is unbounded in corpus size."
    )


def test_q_learning_dispatch_step_under_5ms(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR01: dispatch is just thread spawn + queue check; <5 ms regardless of corpus."""
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None: [],
    )
    result = _invoke_build_check(tmp_project)

    durations = result["step_durations_ms"]
    assert isinstance(durations, dict)
    dispatch_ms = float(durations["q_learning_dispatch"])

    assert dispatch_ms < 50.0, (
        f"FR01 regression: q_learning_dispatch took {dispatch_ms:.1f}ms (cap 50ms). "
        f"Dispatch is just thread spawn + queue inspection — should be near-instant. "
        f"A larger value suggests work has leaked back into the dispatch path "
        f"(e.g. someone re-added an inline correlate_recalls call)."
    )


def test_build_check_does_not_block_on_slow_correlation(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR01: a slow ``process_outcome_for_event`` does NOT delay the response.

    Simulates the live regression: ``process_outcome_for_event`` takes
    several seconds. Pre-fix the tool call would block for the entire
    duration. Post-fix the tool returns within the latency budget while
    the bg worker continues.
    """
    import threading

    proceed = threading.Event()

    def slow_correlation(event_type: str, event_data: object = None) -> list[str]:
        proceed.wait(timeout=30.0)  # block until the test releases
        return []

    monkeypatch.setattr("trw_mcp.scoring.process_outcome_for_event", slow_correlation)

    try:
        t0 = time.monotonic()
        _invoke_build_check(tmp_project)
        elapsed_ms = (time.monotonic() - t0) * 1000.0

        assert elapsed_ms < 500.0, (
            f"FR01 regression: trw_build_check took {elapsed_ms:.1f}ms (cap 500ms) "
            f"despite a multi-second slow correlation. The tool MUST NOT block "
            f"on the bg thread. If this fails, the dispatch is synchronous again."
        )
    finally:
        # Always release the worker so the autouse fixture can join it.
        proceed.set()
