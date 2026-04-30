"""Shared fixtures and helpers for split ceremony tool tests."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture()
def trw_project(tmp_path: Path) -> Path:
    """Create a minimal .trw/ project structure."""
    trw_dir = tmp_path / ".trw"
    learnings_dir = trw_dir / "learnings" / "entries"
    learnings_dir.mkdir(parents=True)
    (trw_dir / "reflections").mkdir()
    (trw_dir / "context").mkdir()

    (learnings_dir / "2026-02-10-sample.yaml").write_text(
        "id: L-sample001\nsummary: Test learning\ndetail: Some detail\n"
        "status: active\nimpact: 0.8\ntags:\n  - testing\n"
        "access_count: 0\nq_observations: 0\nq_value: 0.5\n"
        "source_type: agent\nsource_identity: ''\n",
        encoding="utf-8",
    )
    (trw_dir / "learnings" / "index.yaml").write_text(
        "total_entries: 1\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory structure."""
    d = tmp_path / ".trw" / "runs" / "task" / "20260211T120000Z-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: test-run\nstatus: active\nphase: implement\ntask: test-task\n",
        encoding="utf-8",
    )
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


@contextlib.contextmanager
def _apply_stubs(stubs: dict[str, Any]) -> Generator[None, None, None]:
    """Enter all ``patch`` context managers in *stubs* as a single block."""
    with contextlib.ExitStack() as stack:
        for stub in stubs.values():
            stack.enter_context(stub)
        yield


def _make_deferred_trw_dir(tmp_path: Path) -> Path:
    """Create the minimal .trw structure needed by ``_run_deferred_steps``."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "logs").mkdir(parents=True, exist_ok=True)
    return trw_dir


def _stub_all_deferred_steps() -> dict[str, Any]:
    """Return ``patch`` context managers that stub every deferred step."""
    noop: dict[str, object] = {"status": "skipped"}
    return {
        "_step_auto_prune": patch(
            "trw_mcp.tools._deferred_delivery._step_auto_prune",
            return_value=noop,
        ),
        "_step_consolidation": patch(
            "trw_mcp.tools._deferred_delivery._step_consolidation",
            return_value=noop,
        ),
        "_step_tier_sweep": patch(
            "trw_mcp.tools._deferred_delivery._step_tier_sweep",
            return_value=noop,
        ),
        "_do_index_sync": patch(
            "trw_mcp.tools._deferred_delivery._do_index_sync",
            return_value=noop,
        ),
        "_step_auto_progress": patch(
            "trw_mcp.tools._deferred_delivery._step_auto_progress",
            return_value=noop,
        ),
        "_step_publish_learnings": patch(
            "trw_mcp.tools._deferred_delivery._step_publish_learnings",
            return_value=noop,
        ),
        "_step_outcome_correlation": patch(
            "trw_mcp.tools._deferred_delivery._step_outcome_correlation",
            return_value=noop,
        ),
        "_step_recall_outcome": patch(
            "trw_mcp.tools._deferred_delivery._step_recall_outcome",
            return_value=noop,
        ),
        "_step_telemetry": patch(
            "trw_mcp.tools._deferred_delivery._step_telemetry",
            return_value=noop,
        ),
        "_step_batch_send": patch(
            "trw_mcp.tools._deferred_delivery._step_batch_send",
            return_value=noop,
        ),
        "_step_trust_increment": patch(
            "trw_mcp.tools._deferred_delivery._step_trust_increment",
            return_value=noop,
        ),
        "_step_ceremony_feedback": patch(
            "trw_mcp.tools._deferred_delivery._step_ceremony_feedback",
            return_value=noop,
        ),
    }


def _read_deferred_log(trw_dir: Path) -> dict[str, Any]:
    """Read the latest deferred delivery log entry."""
    log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
    assert log_path.exists(), "deferred-deliver.jsonl was not written"
    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    return json.loads(lines[-1])  # type: ignore[no-any-return]
