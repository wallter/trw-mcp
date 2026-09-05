"""WD-05 — an UNREADABLE deliver_gate_mode must not turn the delivery gate off.

External audit + independent skeptic (2026-09-04). ``resolve_gate_mode`` mapped
ANY exception from ``get_config()`` to ``"advisory"``, and
``resolve_deliver_gate_decision`` returned ``False`` for ``advisory`` BEFORE the
PRD-CORE-246 change-evidence clause was ever evaluated. So one unparseable
``.trw/config.yaml`` disabled the gate for a coding run with dozens of changed
files — and it did so most sharply under ``TRW_CONFIG_STRICT=1``, where the
loader deliberately RAISES (verified against
``models/config/_loader.py::_build_config``) instead of silently reverting to
defaults. Opting into strict config handling made the delivery gate WEAKER.

The fix keeps PRD-CORE-213-NFR02 intact: a project that explicitly configures
``advisory`` still gets ``advisory``. Only the case where nobody could read the
value at all falls back to the field's declared default and keeps evaluating
change evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._deliver_gate_mode import (
    resolve_deliver_gate_decision,
    resolve_gate_mode,
    resolve_gate_mode_with_source,
)
from trw_mcp.tools._delivery_helpers import check_delivery_gates

pytestmark = pytest.mark.integration


def _raise_config() -> TRWConfig:
    """Exactly what ``get_config`` does under TRW_CONFIG_STRICT with a corrupt file."""
    raise StateError("Failed to read YAML: .trw/config.yaml is corrupt")


def _make_run(tmp_path: Path, task_type: str, *, modified: int) -> Path:
    writer = FileStateWriter()
    run_dir = tmp_path / ".trw" / "runs" / "wd05" / "20260904T000000Z-cccc3333"
    (run_dir / "meta").mkdir(parents=True)
    writer.write_yaml(
        run_dir / "meta" / "run.yaml",
        {
            "run_id": "20260904T000000Z-cccc3333",
            "task": "wd05",
            "status": "active",
            "phase": "deliver",
            "task_type": task_type,
        },
    )
    writer.append_jsonl(run_dir / "meta" / "events.jsonl", {"event": "run_init", "task": "wd05"})
    for i in range(modified):
        writer.append_jsonl(run_dir / "meta" / "events.jsonl", {"event": "file_modified", "file": f"src/w{i}.py"})
    return run_dir


def test_fallback_is_the_declared_field_default_not_advisory(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable config yields the FIELD's declared default, introspected."""
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", _raise_config)

    mode, from_fallback = resolve_gate_mode_with_source("docs")

    declared = TRWConfig.model_fields["deliver_gate_mode"].default
    assert from_fallback is True
    assert mode == declared
    assert mode != "advisory"
    # Read from the model, never copied: this fails if someone hardcodes a
    # literal here and later changes the shipped default.
    assert resolve_gate_mode("docs") == declared


def test_fallback_mode_still_evaluates_the_change_threshold() -> None:
    """``mode_from_fallback`` forces the change-evidence clause whatever the mode names."""
    assert (
        resolve_deliver_gate_decision(
            mode="advisory",
            task_type="docs",
            build_check_missing=True,
            files_changed=40,
            mode_from_fallback=True,
        )
        is True
    )
    # A READABLE advisory config is untouched — NFR02, and the non-vacuity
    # counterpart that keeps this from being "block everything".
    assert (
        resolve_deliver_gate_decision(
            mode="advisory",
            task_type="docs",
            build_check_missing=True,
            files_changed=40,
            mode_from_fallback=False,
        )
        is False
    )


def test_audit_repro_corrupt_config_coding_run_is_blocked(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reported repro: corrupt config + 40 changed files + coding -> BLOCKED.

    Pre-fix ``resolve_gate_mode`` returned ``advisory`` and
    ``resolve_deliver_gate_decision`` short-circuited to ``False`` on line 133
    before ``_meets_change_threshold`` ran, so this delivered clean.
    """
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", _raise_config)

    run_dir = _make_run(tmp_project, "coding", modified=40)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")

    assert result.get("delivery_blocked"), result
    assert result.get("blocked_task_type") == "coding"
    assert result.get("missing_gate") == "build_check"


def test_repro_is_discriminating_against_the_pre_fix_resolver(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore ONLY the pre-fix resolver result and the same run delivers clean.

    Attribution: this pins the block above to the mode-resolution change rather
    than to some other gate in ``check_delivery_gates`` happening to fire. The
    pre-fix ``resolve_gate_mode`` answered ``"advisory"`` for an unreadable
    config and carried no fallback signal.
    """
    monkeypatch.setattr(
        "trw_mcp.tools._deliver_gate_mode.resolve_gate_mode_with_source",
        lambda _task_type: ("advisory", False),
    )

    run_dir = _make_run(tmp_project, "coding", modified=40)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")

    assert not result.get("delivery_blocked")


def test_explicit_advisory_config_still_delivers(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A project that CONFIGURED advisory keeps it — the fix is scoped to unreadable."""
    cfg = TRWConfig().model_copy(update={"deliver_gate_mode": "advisory"})
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "coding", modified=40)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")

    assert not result.get("delivery_blocked")
    assert result.get("build_gate_warning")


def test_status_preview_agrees_with_the_gate_on_an_unreadable_config(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preview parity: trw_status must not report "not blocked" for a delivery that blocks."""
    from trw_mcp.tools._orchestration_gate_scan import _build_gate_would_block

    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", _raise_config)

    run_dir = _make_run(tmp_project, "coding", modified=40)
    events = FileStateReader().read_jsonl(run_dir / "meta" / "events.jsonl")

    assert _build_gate_would_block(run_dir, missing_build=True, events=events) is True
