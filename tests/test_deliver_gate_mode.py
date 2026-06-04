"""Tests for PRD-CORE-184-FR03 — task-type-aware deliver gate mode.

The ``deliver_gate_mode`` config flag (advisory | block_coding | block_all)
governs whether a missing passing build check blocks delivery, conditioned on
the run's ``task_type``. Default is ``advisory`` — ZERO behavior change, proven
by the regression tests below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import get_config
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._delivery_helpers import (
    check_delivery_gates,
    resolve_deliver_gate_decision,
)


def _make_run(tmp_path: Path, task_type: str, *, build_passed: bool) -> Path:
    """Create a run dir with a work event and optional passing build check."""
    writer = FileStateWriter()
    run_dir = tmp_path / "docs" / "t" / "runs" / "20260602T000000Z-aaaa1111"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    writer.write_yaml(
        meta / "run.yaml",
        {
            "run_id": "20260602T000000Z-aaaa1111",
            "task": "t",
            "status": "active",
            "phase": "deliver",
            "task_type": task_type,
        },
    )
    writer.append_jsonl(meta / "events.jsonl", {"event": "run_init", "task": "t"})
    writer.append_jsonl(meta / "events.jsonl", {"event": "file_modified", "path": "x.py"})
    if build_passed:
        writer.append_jsonl(
            meta / "events.jsonl",
            {"event": "build_check_complete", "tests_passed": True, "static_checks_clean": True},
        )
    return run_dir


# ── resolve_deliver_gate_decision (pure dispatch) ───────────────────────────


@pytest.mark.parametrize(
    ("mode", "task_type", "build_missing", "expect_block"),
    [
        # advisory never blocks, regardless of task type / build state
        ("advisory", "coding", True, False),
        ("advisory", "rca", True, False),
        ("advisory", "docs", True, False),
        # block_coding blocks coding/rca/eval, advisory for docs/research/planning/unknown
        ("block_coding", "coding", True, True),
        ("block_coding", "rca", True, True),
        ("block_coding", "eval", True, True),
        ("block_coding", "docs", True, False),
        ("block_coding", "research", True, False),
        ("block_coding", "planning", True, False),
        ("block_coding", "unknown", True, False),
        # block_all blocks every build-artifact type but NOT docs/research/planning
        ("block_all", "coding", True, True),
        ("block_all", "rca", True, True),
        ("block_all", "eval", True, True),
        ("block_all", "docs", True, False),
        ("block_all", "research", True, False),
        ("block_all", "planning", True, False),
        ("block_all", "unknown", True, False),
        # a passing build check is never blocked
        ("block_coding", "coding", False, False),
        ("block_all", "coding", False, False),
    ],
)
def test_resolve_deliver_gate_decision(
    mode: str, task_type: str, build_missing: bool, expect_block: bool
) -> None:
    blocked = resolve_deliver_gate_decision(
        mode=mode,
        task_type=task_type,
        build_check_missing=build_missing,
    )
    assert blocked is expect_block


def test_resolve_deliver_gate_decision_unknown_mode_fails_open() -> None:
    """An unrecognised mode must not block (fail-open / no regression)."""
    assert (
        resolve_deliver_gate_decision(mode="bogus", task_type="coding", build_check_missing=True)
        is False
    )


# ── check_delivery_gates integration (reads run.yaml + config) ──────────────


def test_block_coding_blocks_coding_task(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR03 AC: coding + no build check + block_coding -> delivery_blocked set."""
    cfg = get_config()
    object.__setattr__(cfg, "deliver_gate_mode", "block_coding")
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "coding", build_passed=False)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")
    assert result.get("delivery_blocked")
    assert result.get("missing_gate") == "build_check"


def test_block_coding_advisory_for_docs_task(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR03 AC: docs + no build check + block_coding -> advisory only (no block)."""
    cfg = get_config()
    object.__setattr__(cfg, "deliver_gate_mode", "block_coding")
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "docs", build_passed=False)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")
    assert not result.get("delivery_blocked")
    # build_gate_warning (advisory) may still be present — that's the current behavior
    assert "build_gate_warning" in result


def test_advisory_default_no_regression(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION: default deliver_gate_mode=advisory never sets delivery_blocked."""
    cfg = get_config()
    # Do NOT set deliver_gate_mode — must default to advisory.
    assert cfg.deliver_gate_mode == "advisory"
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "coding", build_passed=False)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")
    assert not result.get("delivery_blocked")
    # The pre-existing advisory build_gate_warning is unchanged.
    assert "build_gate_warning" in result


def test_block_coding_passing_build_not_blocked(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = get_config()
    object.__setattr__(cfg, "deliver_gate_mode", "block_coding")
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "coding", build_passed=True)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")
    assert not result.get("delivery_blocked")
    assert "build_gate_warning" not in result
