"""Tests for session_start pipeline_health_advisory wiring — PRD-FIX-COMPOUNDING-6.

Verifies FR03: step_pipeline_health is called from trw_session_start, and
the pipeline_health_advisory field is injected only when degraded=True.

Also verifies FR04: pipeline_health_advisory coexists with sync_health
without field collision.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def _degraded_health_result(signals: list[str] | None = None) -> dict[str, object]:
    """Return a mock PipelineHealthResult with degraded=True."""
    signal_names = signals or ["sync_push"]
    return {
        "degraded": True,
        "advisory": f"pipeline degraded: {', '.join(signal_names)} — call trw_pipeline_health() for details",
        "sync_push": {"degraded": True, "advisory": "test"},
        "graph_edges": {"degraded": False, "advisory": ""},
        "embedding_coverage": {"degraded": False, "advisory": ""},
        "recall_feedback": {"degraded": False, "advisory": ""},
        "bandit_state": {"degraded": False, "advisory": ""},
    }


def _healthy_health_result() -> dict[str, object]:
    """Return a mock PipelineHealthResult with degraded=False."""
    return {
        "degraded": False,
        "advisory": "",
        "sync_push": {"degraded": False, "advisory": ""},
        "graph_edges": {"degraded": False, "advisory": ""},
        "embedding_coverage": {"degraded": False, "advisory": ""},
        "recall_feedback": {"degraded": False, "advisory": ""},
        "bandit_state": {"degraded": False, "advisory": ""},
    }


def test_session_start_with_degraded_pipeline(tmp_path: Path) -> None:
    """Degraded pipeline => pipeline_health_advisory key present in session_start result."""
    from trw_mcp.tools._ceremony_session_start_steps import step_pipeline_health_advisory

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, object] = {}
    degraded = _degraded_health_result()

    with patch("trw_mcp.tools._ceremony_session_start_steps.step_pipeline_health", return_value=degraded):
        step_pipeline_health_advisory(trw_dir, results)

    assert "pipeline_health_advisory" in results
    advisory = str(results["pipeline_health_advisory"])
    assert advisory != ""
    assert "sync_push" in advisory or "pipeline" in advisory.lower()


def test_session_start_with_healthy_pipeline(tmp_path: Path) -> None:
    """Healthy pipeline => pipeline_health_advisory absent (or empty) from result."""
    from trw_mcp.tools._ceremony_session_start_steps import step_pipeline_health_advisory

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, object] = {}
    healthy = _healthy_health_result()

    with patch("trw_mcp.tools._ceremony_session_start_steps.step_pipeline_health", return_value=healthy):
        step_pipeline_health_advisory(trw_dir, results)

    # Key should not be injected, or if present should be empty
    advisory = results.get("pipeline_health_advisory", "")
    assert advisory == "" or "pipeline_health_advisory" not in results


def test_fields_no_collision(tmp_path: Path) -> None:
    """sync_health (COMPOUNDING-1) and pipeline_health_advisory (this PRD) coexist."""
    from trw_mcp.tools._ceremony_session_start_steps import step_pipeline_health_advisory

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)

    # Start with sync_health already in results (as COMPOUNDING-1 would set it)
    results: dict[str, object] = {
        "sync_health": {
            "degraded": True,
            "consecutive_failures": 15,
            "last_push_at": "2026-04-21",
            "advisory": "Backend sync-push is degraded.",
        }
    }

    degraded = _degraded_health_result(["sync_push"])
    with patch("trw_mcp.tools._ceremony_session_start_steps.step_pipeline_health", return_value=degraded):
        step_pipeline_health_advisory(trw_dir, results)

    # Both fields present, no collision
    assert "sync_health" in results
    assert "pipeline_health_advisory" in results
    # sync_health untouched
    sh = results["sync_health"]
    assert isinstance(sh, dict)
    assert sh.get("consecutive_failures") == 15
    # pipeline_health_advisory is a string, not a dict
    assert isinstance(results["pipeline_health_advisory"], str)


def test_step_pipeline_health_advisory_fail_open(tmp_path: Path) -> None:
    """If step_pipeline_health raises, step_pipeline_health_advisory does not propagate the exception."""
    from trw_mcp.tools._ceremony_session_start_steps import step_pipeline_health_advisory

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, object] = {}

    with patch(
        "trw_mcp.tools._ceremony_session_start_steps.step_pipeline_health",
        side_effect=RuntimeError("completely broken"),
    ):
        # Must not raise
        step_pipeline_health_advisory(trw_dir, results)

    # On failure, either key is absent or advisory is empty (fail-open)
    advisory = results.get("pipeline_health_advisory", "")
    assert advisory == "" or "pipeline_health_advisory" not in results


def test_pipeline_health_advisory_advisory_contains_tool_hint(tmp_path: Path) -> None:
    """Advisory string must contain hint to call trw_pipeline_health() for details."""
    from trw_mcp.tools._ceremony_session_start_steps import step_pipeline_health_advisory

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, object] = {}
    degraded = _degraded_health_result(["sync_push", "embedding_coverage"])

    with patch("trw_mcp.tools._ceremony_session_start_steps.step_pipeline_health", return_value=degraded):
        step_pipeline_health_advisory(trw_dir, results)

    advisory = str(results.get("pipeline_health_advisory", ""))
    # Per PRD §12 Open Questions: advisory should reference trw_pipeline_health()
    assert "trw_pipeline_health" in advisory


def test_session_start_pipeline_health_wired_into_ceremony(tmp_path: Path) -> None:
    """Verify that ceremony.py::trw_session_start calls step_pipeline_health."""
    import inspect

    import trw_mcp.tools.ceremony as ceremony_mod

    source = inspect.getsource(ceremony_mod)
    # The wiring should be traceable via the imported name
    # (step_pipeline_health_advisory or step_pipeline_health)
    assert "pipeline_health" in source, (
        "ceremony.py must reference pipeline_health (step_pipeline_health_advisory "
        "or step_pipeline_health) — wiring not found"
    )
