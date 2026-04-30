from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory structure for review tests."""
    directory = tmp_path / "docs" / "task" / "runs" / "20260301T120000Z-review-modes-test"
    meta = directory / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: review-modes-test\nstatus: active\nphase: review\ntask_name: review-modes-task\n",
        encoding="utf-8",
    )
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return directory


def _make_config(
    *,
    cross_model_enabled: bool = False,
    cross_model_provider: str = "gemini-2.5-pro",
    confidence_threshold: int = 80,
) -> TRWConfig:
    """Build a minimal TRWConfig for testing review modes."""
    return TRWConfig(
        cross_model_review_enabled=cross_model_enabled,
        cross_model_provider=cross_model_provider,
        review_confidence_threshold=confidence_threshold,
    )
