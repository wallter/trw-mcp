"""Shared support for split build tool tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateWriter

pytest.importorskip(
    "trw_mcp.tools.build._subprocess",
    reason="PRD-CORE-098: subprocess modules removed — old tests obsolete",
)


def _write_build_cache(
    trw_dir: Path,
    *,
    tests_passed: bool = True,
    mypy_clean: bool = True,
    coverage_pct: float = 90.0,
    scope: str = "full",
    timestamp: str | None = None,
) -> Path:
    """Helper to write a build-status.yaml for phase gate tests."""
    context_dir = trw_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    cache_path = context_dir / "build-status.yaml"
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    writer = FileStateWriter()
    writer.write_yaml(
        cache_path,
        {
            "tests_passed": tests_passed,
            "mypy_clean": mypy_clean,
            "coverage_pct": coverage_pct,
            "test_count": 100,
            "failure_count": 0 if tests_passed else 3,
            "failures": [] if tests_passed else ["FAILED test_a", "FAILED test_b", "FAILED test_c"],
            "timestamp": ts,
            "scope": scope,
            "duration_secs": 30.0,
        },
    )
    return cache_path
