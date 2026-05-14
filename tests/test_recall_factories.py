"""PRD-FIX-085 FR05: named recall factories cover the major call sites.

Pre-fix, 10+ call sites used recall_learnings() with divergent
parameter combinations. Each call site was its own bug surface.

Post-fix, named factories enumerate the actual usage patterns:
- recall_baseline_high_impact: session_start wildcard high-impact
- recall_focused: session_start focused on user query
- recall_recent_bypass: session_start L-fovv recency bypass
- recall_for_nudge_pool: nudge content selection
- recall_for_review_tags: claude_md review/publish
- recall_for_learning_injection: task-relevant active learnings
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from trw_mcp.state import recall_factories


def _captured_call(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Side effect that records args/kwargs and returns a sentinel result."""
    _captured_call.args = args  # type: ignore[attr-defined]
    _captured_call.kwargs = kwargs  # type: ignore[attr-defined]
    return [{"id": "L-sentinel"}]


def test_recall_baseline_high_impact_pins_constants(tmp_path: Path) -> None:
    """recall_baseline_high_impact uses query='*', min_impact=0.7, compact=True."""
    with patch.object(recall_factories, "_default_recall", return_value=_captured_call):
        recall_factories.recall_baseline_high_impact(tmp_path, max_results=5)
    kwargs = _captured_call.kwargs  # type: ignore[attr-defined]
    assert kwargs["query"] == "*"
    assert kwargs["min_impact"] == 0.7
    assert kwargs["compact"] is True
    assert kwargs["max_results"] == 5
    assert kwargs["allow_cold_embedding_init"] is False


def test_recall_focused_pins_min_impact(tmp_path: Path) -> None:
    """recall_focused uses min_impact=0.3 by default and propagates query."""
    with patch.object(recall_factories, "_default_recall", return_value=_captured_call):
        recall_factories.recall_focused(tmp_path, "auth scoring", max_results=10)
    kwargs = _captured_call.kwargs  # type: ignore[attr-defined]
    assert kwargs["query"] == "auth scoring"
    assert kwargs["min_impact"] == 0.3
    assert kwargs["compact"] is True
    assert kwargs["max_results"] == 10


def test_recall_recent_bypass_returns_full_entries(tmp_path: Path) -> None:
    """recall_recent_bypass uses compact=False so callers can date-filter."""
    with patch.object(recall_factories, "_default_recall", return_value=_captured_call):
        recall_factories.recall_recent_bypass(tmp_path, max_results=20, min_impact=0.3)
    kwargs = _captured_call.kwargs  # type: ignore[attr-defined]
    assert kwargs["query"] == "*"
    assert kwargs["min_impact"] == 0.3
    assert kwargs["compact"] is False


def test_recall_for_nudge_pool_uses_compact_false(tmp_path: Path) -> None:
    """recall_for_nudge_pool uses compact=False because rendering needs summaries."""
    with patch.object(recall_factories, "_default_recall", return_value=_captured_call):
        recall_factories.recall_for_nudge_pool(tmp_path, query="*", tags=["audit"], min_impact=0.5, max_results=10)
    kwargs = _captured_call.kwargs  # type: ignore[attr-defined]
    assert kwargs["query"] == "*"
    assert kwargs["tags"] == ["audit"]
    assert kwargs["min_impact"] == 0.5
    assert kwargs["max_results"] == 10
    assert kwargs["compact"] is False


def test_recall_for_review_tags_pins_status_active(tmp_path: Path) -> None:
    """recall_for_review_tags pins status='active'."""
    with patch.object(recall_factories, "_default_recall", return_value=_captured_call):
        recall_factories.recall_for_review_tags(tmp_path, tags=["pattern"], min_impact=0.7, max_results=20)
    kwargs = _captured_call.kwargs  # type: ignore[attr-defined]
    assert kwargs["tags"] == ["pattern"]
    assert kwargs["min_impact"] == 0.7
    assert kwargs["max_results"] == 20
    assert kwargs["status"] == "active"


def test_recall_for_learning_injection_pins_status_active(tmp_path: Path) -> None:
    """recall_for_learning_injection pins status='active' and propagates task description."""
    with patch.object(recall_factories, "_default_recall", return_value=_captured_call):
        recall_factories.recall_for_learning_injection(
            tmp_path, "fix auth bug", tags=["security"], min_impact=0.5, max_results=10
        )
    kwargs = _captured_call.kwargs  # type: ignore[attr-defined]
    assert kwargs["query"] == "fix auth bug"
    assert kwargs["tags"] == ["security"]
    assert kwargs["min_impact"] == 0.5
    assert kwargs["max_results"] == 10
    assert kwargs["status"] == "active"


def test_known_callers_use_factories() -> None:
    """Source-grep audit: the 4 migrated call sites use a factory function.

    Pre-fix these called recall_learnings(...) directly with divergent params.
    Post-fix they call a named factory. Future regressions of "ad-hoc
    parameter drift" are caught by this test.
    """
    import subprocess

    result = subprocess.run(
        [
            "grep",
            "-rn",
            "--include=*.py",
            r"recall_learnings(",
            "src/",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode not in (0, 1):
        pytest.skip(f"grep failed: {result.stderr}")

    # The 5 migrated call sites must NO LONGER call recall_learnings directly.
    # Allowed contexts: the wrapper definition itself, imports, comments,
    # and learning_injection.py's local wrapper (intentional file-level DRY).
    forbidden_files = {
        "src/trw_mcp/tools/_ceremony_status.py",
        "src/trw_mcp/state/ceremony_nudge.py",
        "src/trw_mcp/state/claude_md/_sync.py",
        "src/trw_mcp/tools/_session_recall_helpers.py",
    }
    offenders: list[str] = []
    for line in result.stdout.splitlines():
        for path in forbidden_files:
            if line.startswith(path + ":"):
                # Only flag actual call lines (not imports / aliases / comments).
                _, _, src = line.split(":", 2)
                stripped = src.strip()
                if stripped.startswith("#"):
                    continue
                if "from " in stripped or "import " in stripped:
                    continue
                if "as adapter_recall" in stripped or "as recall_learnings" in stripped:
                    continue
                if "recall_learnings(" in stripped:
                    offenders.append(line)
    assert offenders == [], (
        "FR05: migrated call sites must use a recall_factories factory, "
        "not recall_learnings(...) directly:\n" + "\n".join(offenders)
    )
