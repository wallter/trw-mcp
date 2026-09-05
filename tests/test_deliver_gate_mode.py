"""Tests for PRD-CORE-184-FR03 + PRD-CORE-246-FR03 — the deliver gate mode.

The ``deliver_gate_mode`` config flag (advisory | block_coding | block_all)
governs whether a missing passing build check blocks delivery. Default is
``block_coding`` (flipped from ``advisory`` 2026-06-10). Since PRD-CORE-246-FR03
the block predicate is a DISJUNCTION: the run's ``task_type`` expects a build
artifact (coding/rca/eval) OR the session recorded at least
``deliver_gate_unclassified_change_threshold`` distinct modified files. The
task-type-only expectations that used to live here encoded the superseded
never-block-on-unknown rule and are updated in place, with the zero-change case
retained as the proof that a ceremony-only run still stays advisory.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests._ceremony_helpers import make_ceremony_server
from trw_mcp.models.config import get_config
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._delivery_helpers import (
    check_delivery_gates,
    resolve_deliver_gate_decision,
)


def _make_run(
    tmp_path: Path,
    task_type: str,
    *,
    build_passed: bool,
    file_modified_count: int = 1,
) -> Path:
    """Create a run dir with ``file_modified_count`` distinct modified files.

    ``file_modified_count=0`` is the ceremony-only shape: the build gate still
    warns (it is independent of work events), but PRD-CORE-246's change-evidence
    clause is unarmed, so an advisory task type stays advisory.
    """
    writer = FileStateWriter()
    run_dir = tmp_path / ".trw" / "runs" / "t" / "20260602T000000Z-aaaa1111"
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
    for i in range(file_modified_count):
        writer.append_jsonl(meta / "events.jsonl", {"event": "file_modified", "file": f"x{i}.py"})
    if build_passed:
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "build_check_complete",
                "test_count": 12,
                "scope": "pytest tests",
                "tests_passed": True,
                "static_checks_clean": True,
            },
        )
    return run_dir


def _call_deliver(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    task_type: str,
    mode: str,
    file_modified_count: int = 1,
    **deliver_kwargs: object,
) -> dict[str, Any]:
    """Exercise the public trw_deliver tool with heavyweight steps stubbed."""
    trw_dir = tmp_project / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "reflections").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    run_dir = _make_run(tmp_project, task_type, build_passed=False, file_modified_count=file_modified_count)
    cfg = get_config()
    object.__setattr__(cfg, "deliver_gate_mode", mode)

    tools = make_ceremony_server(monkeypatch, tmp_project)
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)
    monkeypatch.setattr("trw_mcp.tools.ceremony.get_config", lambda: cfg)
    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_project)
    monkeypatch.setattr("trw_mcp.tools.ceremony._step_checkpoint", lambda *_a, **_kw: {"status": "success"})
    monkeypatch.setattr("trw_mcp.tools._ceremony_deliver_tool._probe_integrity", lambda *_a, **_kw: None)
    monkeypatch.setattr("trw_mcp.tools._ceremony_deliver_tool.step_knowledge_sync", lambda *_a, **_kw: None)
    monkeypatch.setattr("trw_mcp.tools._ceremony_deliver_tool._launch_deferred", lambda *_a, **_kw: "skipped")
    monkeypatch.setattr("trw_mcp.tools._ceremony_deliver_tool._mark_deliver_and_reflect_learning", lambda *_a: None)
    monkeypatch.setattr("trw_mcp.tools._ceremony_deliver_tool._write_nudge_analysis_artifact", lambda *_a: None)

    return tools["trw_deliver"].fn(
        run_path=str(run_dir),
        skip_reflect=True,
        skip_index_sync=True,
        **deliver_kwargs,
    )


# ── resolve_deliver_gate_decision (pure dispatch) ───────────────────────────


@pytest.mark.parametrize(
    ("mode", "task_type", "build_missing", "files_changed", "expect_block"),
    [
        # advisory never blocks, regardless of task type / build state / changes
        ("advisory", "coding", True, 3, False),
        ("advisory", "rca", True, 3, False),
        ("advisory", "docs", True, 3, False),
        # block_coding blocks the build-artifact types on the task-type clause
        # alone, even with zero recorded changes.
        ("block_coding", "coding", True, 0, True),
        ("block_coding", "rca", True, 0, True),
        ("block_coding", "eval", True, 0, True),
        # ...and the remaining types stay advisory ONLY while nothing changed.
        ("block_coding", "docs", True, 0, False),
        ("block_coding", "research", True, 0, False),
        ("block_coding", "planning", True, 0, False),
        ("block_coding", "unknown", True, 0, False),
        # PRD-CORE-246-FR03: recorded file modifications arm the gate for every
        # task type. This supersedes the task-type-only expectations above.
        ("block_coding", "docs", True, 1, True),
        ("block_coding", "research", True, 3, True),
        ("block_coding", "planning", True, 3, True),
        ("block_coding", "unknown", True, 3, True),
        # block_all uses the same disjunction.
        ("block_all", "coding", True, 0, True),
        ("block_all", "rca", True, 0, True),
        ("block_all", "eval", True, 0, True),
        ("block_all", "docs", True, 0, False),
        ("block_all", "research", True, 0, False),
        ("block_all", "planning", True, 0, False),
        ("block_all", "unknown", True, 0, False),
        ("block_all", "unknown", True, 3, True),
        # a passing build check is never blocked, however much changed
        ("block_coding", "coding", False, 9, False),
        ("block_all", "coding", False, 9, False),
        # NFR02: an uncomputable count is treated as meeting the threshold.
        ("block_coding", "unknown", True, None, True),
        ("block_all", "docs", True, None, True),
        ("advisory", "unknown", True, None, False),
    ],
)
def test_resolve_deliver_gate_decision(
    mode: str, task_type: str, build_missing: bool, files_changed: int | None, expect_block: bool
) -> None:
    blocked = resolve_deliver_gate_decision(
        mode=mode,
        task_type=task_type,
        build_check_missing=build_missing,
        files_changed=files_changed,
    )
    assert blocked is expect_block


def test_resolve_deliver_gate_decision_unknown_mode_fails_open() -> None:
    """An unrecognised mode must not block (fail-open / no regression)."""
    assert (
        resolve_deliver_gate_decision(mode="bogus", task_type="coding", build_check_missing=True, files_changed=9)
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


def test_block_coding_advisory_for_unchanged_docs_task(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """docs + no build check + NO recorded change -> advisory only (no block).

    PRD-CORE-246-FR03 narrowed this case: it is the ZERO-CHANGE run that stays
    advisory, not the docs label. The changed-file companion below proves the
    same task type blocks once the session recorded modifications.
    """
    cfg = get_config()
    object.__setattr__(cfg, "deliver_gate_mode", "block_coding")
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "docs", build_passed=False, file_modified_count=0)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")
    assert not result.get("delivery_blocked")
    # build_gate_warning (advisory) may still be present — that's the current behavior
    assert "build_gate_warning" in result


def test_block_coding_blocks_docs_task_that_modified_files(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD-CORE-246-FR03: change evidence blocks a non-build-artifact task type."""
    cfg = get_config()
    object.__setattr__(cfg, "deliver_gate_mode", "block_coding")
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "docs", build_passed=False, file_modified_count=3)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")
    assert result.get("delivery_blocked")
    assert result.get("blocked_task_type") == "docs"


def test_block_coding_default_blocks_coding_missing_build(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default deliver_gate_mode=block_coding blocks a coding run without build evidence."""
    cfg = get_config()
    # Do NOT set deliver_gate_mode — must default to block_coding (2026-06-10 flip).
    assert cfg.deliver_gate_mode == "block_coding"
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "coding", build_passed=False)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")
    assert result.get("delivery_blocked")
    assert result.get("missing_gate") == "build_check"


def test_explicit_advisory_restores_warn_only(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit deliver_gate_mode=advisory never sets delivery_blocked (opt-out path)."""
    cfg = get_config()
    object.__setattr__(cfg, "deliver_gate_mode", "advisory")
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "coding", build_passed=False)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")
    assert not result.get("delivery_blocked")
    # The pre-existing advisory build_gate_warning is unchanged.
    assert "build_gate_warning" in result


def test_block_coding_passing_build_not_blocked(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = get_config().model_copy(update={"deliver_gate_mode": "block_coding", "evidence_receipt_mode": "observe"})
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)
    monkeypatch.setattr("trw_mcp.tools._delivery_helpers.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "coding", build_passed=True)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")
    assert not result.get("delivery_blocked")
    assert "build_gate_warning" not in result


# ── public trw_deliver end-to-end policy preservation ─────────────────────


@pytest.mark.parametrize(
    ("task_type", "expect_block"),
    [
        ("coding", True),
        ("rca", True),
        ("eval", True),
        ("docs", False),
        ("research", False),
        ("planning", False),
        ("unknown", False),
    ],
)
def test_trw_deliver_preserves_default_task_type_policy(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_type: str,
    expect_block: bool,
) -> None:
    """The wrapper must not promote an intentionally advisory warning to a block.

    Run with ZERO recorded file modifications so the PRD-CORE-246 change clause
    is unarmed and the task-type clause is the only thing under test.
    """
    result = _call_deliver(tmp_project, monkeypatch, task_type=task_type, mode="block_coding", file_modified_count=0)

    assert bool(result.get("delivery_blocked")) is expect_block
    assert result["success"] is (not expect_block)
    if expect_block:
        assert result.get("blocked_task_type") == task_type
        assert result.get("missing_gate") == "build_check"
    else:
        assert "build_gate_warning" in result
        assert "build_gate_block" not in result
        assert "acceptable_failure_record" not in result


@pytest.mark.parametrize("task_type", ["coding", "rca", "eval", "docs", "research", "planning", "unknown"])
def test_trw_deliver_blocks_every_task_type_that_changed_files(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_type: str,
) -> None:
    """PRD-CORE-246-FR03 end-to-end: change evidence blocks regardless of label."""
    result = _call_deliver(tmp_project, monkeypatch, task_type=task_type, mode="block_coding", file_modified_count=3)

    assert result.get("delivery_blocked")
    assert result.get("blocked_task_type") == task_type
    assert result["success"] is False


@pytest.mark.parametrize(
    ("mode", "expect_block"),
    [
        ("advisory", False),
        ("block_coding", True),
        ("block_all", True),
    ],
)
def test_trw_deliver_respects_each_gate_mode_for_coding(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expect_block: bool,
) -> None:
    result = _call_deliver(tmp_project, monkeypatch, task_type="coding", mode=mode)

    assert bool(result.get("delivery_blocked")) is expect_block
    assert result["success"] is (not expect_block)


def test_hard_build_block_rejects_free_text_override(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _call_deliver(
        tmp_project,
        monkeypatch,
        task_type="coding",
        mode="block_coding",
        allow_unverified=True,
        unverified_reason="tests are probably fine",
    )

    assert result["success"] is False
    assert "acceptable_failure_error" in result
    assert "acceptable-failure schema required" in str(result["acceptable_failure_error"])


def test_hard_build_block_accepts_and_ledgers_structured_override(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = json.dumps(
        {
            "failed_command": "pytest tests -q",
            "residual_risk": "one known integration environment is unavailable",
            "owner": "operator-example",
            "expiry_iso": (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat(),
        }
    )

    result = _call_deliver(
        tmp_project,
        monkeypatch,
        task_type="coding",
        mode="block_coding",
        allow_unverified=True,
        unverified_reason=reason,
    )

    assert result["success"] is True
    assert result["acceptable_failure_record"]["owner"] == "operator-example"
    ledger = list((tmp_project / ".trw" / "overrides").glob("*.yaml"))
    assert len(ledger) == 1
    assert "gate_type: delivery_blocked" in ledger[0].read_text(encoding="utf-8")
