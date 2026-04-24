"""Tests for meta_tune.audit — PRD-HPO-SAFE-001 FR-3/FR-4/FR-14/FR-16."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.meta_tune.audit import (
    AuditAppendError,
    append_audit_entry,
    verify_audit_chain,
)
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig


def _cfg_enabled() -> TRWConfig:
    return TRWConfig(meta_tune=MetaTuneConfig(enabled=True))


def test_append_noop_when_disabled(tmp_path: Path) -> None:
    """FR-7/FR-13: disabled flag yields a no-op append."""
    log = tmp_path / "audit.jsonl"
    result = append_audit_entry(
        log,
        proposal_id="p1",
        surface_classification="advisory",
        gate_decision="approve",
        payload={},
    )
    # enabled=False by default ⇒ no-op
    assert result is None
    assert not log.exists()


def test_append_writes_genesis_with_zero_prev_hash(tmp_path: Path) -> None:
    """First entry uses '0'*64 as prev_hash (per PRD §7.3)."""
    log = tmp_path / "audit.jsonl"
    entry = append_audit_entry(
        log,
        proposal_id="p1",
        surface_classification="advisory",
        gate_decision="approve",
        payload={"k": 1},
        _config=_cfg_enabled(),
    )
    assert entry is not None
    assert entry["prev_hash"] == "0" * 64
    assert len(entry["entry_hash"]) == 64
    assert log.exists()


def test_append_chains_hashes(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    cfg = _cfg_enabled()
    e1 = append_audit_entry(
        log, proposal_id="p1", surface_classification="advisory",
        gate_decision="approve", payload={"k": 1}, _config=cfg,
    )
    e2 = append_audit_entry(
        log, proposal_id="p2", surface_classification="advisory",
        gate_decision="reject", payload={"k": 2}, _config=cfg,
    )
    assert e1 is not None and e2 is not None
    assert e2["prev_hash"] == e1["entry_hash"]


def test_verify_audit_chain_returns_none_when_intact(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    cfg = _cfg_enabled()
    for i in range(5):
        append_audit_entry(
            log, proposal_id=f"p{i}", surface_classification="advisory",
            gate_decision="approve", payload={"i": i}, _config=cfg,
        )
    assert verify_audit_chain(log) is None


def test_verify_audit_chain_detects_tamper(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    cfg = _cfg_enabled()
    for i in range(3):
        append_audit_entry(
            log, proposal_id=f"p{i}", surface_classification="advisory",
            gate_decision="approve", payload={"i": i}, _config=cfg,
        )
    # Mutate row 1 payload directly.
    lines = log.read_text().splitlines()
    obj = json.loads(lines[1])
    obj["payload"]["i"] = 999
    lines[1] = json.dumps(obj)
    log.write_text("\n".join(lines) + "\n")
    broken = verify_audit_chain(log)
    assert broken == 1


def test_verify_audit_chain_detects_broken_prev_link(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    cfg = _cfg_enabled()
    for i in range(3):
        append_audit_entry(
            log, proposal_id=f"p{i}", surface_classification="advisory",
            gate_decision="approve", payload={"i": i}, _config=cfg,
        )
    lines = log.read_text().splitlines()
    obj = json.loads(lines[2])
    obj["prev_hash"] = "f" * 64
    lines[2] = json.dumps(obj)
    log.write_text("\n".join(lines) + "\n")
    broken = verify_audit_chain(log)
    assert broken == 2


def test_verify_audit_chain_empty_file(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    log.write_text("")
    assert verify_audit_chain(log) is None


def test_verify_audit_chain_missing_file(tmp_path: Path) -> None:
    assert verify_audit_chain(tmp_path / "nope.jsonl") is None


def test_append_emits_required_fields(tmp_path: Path) -> None:
    """FR-14 incremental: every row has edit_id, verdict, ts, prev/entry hash."""
    log = tmp_path / "audit.jsonl"
    entry = append_audit_entry(
        log, proposal_id="p1", surface_classification="advisory",
        gate_decision="approve", payload={"v": 1},
        promotion_session_id="sess-A",
        _config=_cfg_enabled(),
    )
    assert entry is not None
    for key in (
        "ts", "proposal_id", "surface_classification", "gate_decision",
        "prev_hash", "entry_hash", "promotion_session_id", "payload",
    ):
        assert key in entry
    # FR-16: promotion_session_id must be present and distinct from proposal_id
    assert entry["promotion_session_id"] == "sess-A"
    assert entry["proposal_id"] == "p1"


def test_append_is_idempotent_safe_concurrent(tmp_path: Path) -> None:
    """Repeated appends produce a valid chain even under rapid succession."""
    log = tmp_path / "audit.jsonl"
    cfg = _cfg_enabled()
    for i in range(20):
        append_audit_entry(
            log, proposal_id=f"p{i}", surface_classification="advisory",
            gate_decision="approve", payload={"i": i}, _config=cfg,
        )
    assert verify_audit_chain(log) is None
    rows = log.read_text().strip().split("\n")
    assert len(rows) == 20
