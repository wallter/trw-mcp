"""Shared fixtures and helpers for split ceremony feedback tests."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.ceremony_feedback import _pending_proposals, record_session_outcome

FeedbackEnv = tuple[Path, TRWConfig]


@pytest.fixture()
def feedback_env(tmp_path: Path) -> Iterator[FeedbackEnv]:
    """Set up a .trw directory for ceremony feedback."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "logs").mkdir(parents=True)

    config = TRWConfig()
    _reset_config(config)
    _pending_proposals.clear()

    yield trw_dir, config

    _reset_config()
    _pending_proposals.clear()


def record_sessions(
    trw_dir: Path,
    scores: Sequence[float],
    *,
    task_name: str = "feat: work",
    build_passed: bool = True,
    coverage_delta: float = 1.0,
    critical_findings: int = 0,
    mutation_passed: bool = True,
    ceremony_tier: str = "STANDARD",
    run_prefix: str = "/r",
    session_prefix: str = "s-",
) -> None:
    """Record a set of ceremony feedback sessions with shared defaults."""
    for index, score in enumerate(scores):
        record_session_outcome(
            trw_dir,
            task_name,
            float(score),
            build_passed,
            coverage_delta,
            critical_findings,
            mutation_passed,
            ceremony_tier,
            f"{run_prefix}/{index}",
            f"{session_prefix}{index}",
        )
