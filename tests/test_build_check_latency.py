"""PRD-FIX-088 FR01: ``trw_build_check`` returns within the latency budget.

Pre-fix: a single call could take 91 s when 2823 entries fell within the
correlation window — every entry was correlated inline before the response
came back. Live measurement 2026-05-04 on the dev shared HTTP MCP server.

R10 supersedes background scheduling: builds no longer initiate temporal
Q attribution. Keep the response-latency guard and verify omitted step timing.

These tests are the latency regression guard for FR01 + NFR03 + NFR08.
"""

from __future__ import annotations

import time
from typing import Any

import pytest


def test_trw_build_check_returns_within_500ms(
    build_check_invoke: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NFR03/NFR08: warm-path p95 < 500 ms.

    The bg worker MAY take seconds to drain; we test that the TOOL CALL
    returns within the cap, not that the worker is done.
    """
    # Stub correlation so the bg worker doesn't actually do work.
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None, **_kw: [],
    )

    t0 = time.monotonic()
    build_check_invoke()
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert elapsed_ms < 500.0, (
        f"NFR03 regression: trw_build_check took {elapsed_ms:.1f}ms (cap 500ms). "
        f"Pre-FIX-088 this was ~91000ms when Q-learning correlated 2823 entries "
        f"inline. If this assertion fails, Q-learning has reverted to inline "
        f"execution OR a new step has been added that is unbounded in corpus size."
    )


def test_retired_q_learning_dispatch_has_no_fabricated_timing(build_check_invoke: Any) -> None:
    """R10: a removed attribution step must not appear as scheduled or timed."""
    result = build_check_invoke()
    assert "q_learning_dispatch" not in result["step_durations_ms"]
    assert "q_learning_deferred" not in result


def test_build_check_does_not_block_on_slow_correlation(
    build_check_invoke: Any,
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

    def slow_correlation(event_type: str, event_data: object = None, **_kw: object) -> list[str]:
        proceed.wait(timeout=30.0)  # block until the test releases
        return []

    monkeypatch.setattr("trw_mcp.scoring.process_outcome_for_event", slow_correlation)

    try:
        t0 = time.monotonic()
        build_check_invoke()
        elapsed_ms = (time.monotonic() - t0) * 1000.0

        assert elapsed_ms < 500.0, (
            f"FR01 regression: trw_build_check took {elapsed_ms:.1f}ms (cap 500ms) "
            f"despite a multi-second slow correlation. The tool MUST NOT block "
            f"on the bg thread. If this fails, the dispatch is synchronous again."
        )
    finally:
        # Always release the worker so the autouse fixture can join it.
        proceed.set()
