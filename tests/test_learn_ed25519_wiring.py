"""Integration test: trw_learn wires Ed25519 provenance (PRD-SEC-001 FR-002).

Sprint-96 carry-forward-b. Verifies that execute_learn appends a signed
provenance record to ``.trw/memory/security/provenance.jsonl`` for every
learning write, and that the chain verifies cleanly under the project's
generated verify key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config import TRWConfig

nacl_signing = pytest.importorskip("nacl.signing")


def _make_config() -> TRWConfig:
    return TRWConfig()


def _fake_store(_trw_dir: Path, *, learning_id: str = "", **kwargs: Any) -> dict[str, object]:
    return {
        "learning_id": learning_id,
        "path": "sqlite://x",
        "status": "recorded",
        "distribution_warning": "",
    }


def test_execute_learn_appends_signed_provenance_record(tmp_path: Path) -> None:
    from trw_mcp.tools._learn_impl import execute_learn

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    result = execute_learn(
        summary="wiring test",
        detail="Verifying Ed25519 provenance chain appends on learn.",
        trw_dir=trw_dir,
        config=_make_config(),
        source_identity="wiring-test-agent",
        _adapter_store=_fake_store,
        _generate_learning_id=lambda: "L-wire-001",
        _save_learning_entry=lambda *a, **kw: tmp_path / "entry.yaml",
        _update_analytics=lambda *a, **kw: None,
        _list_active_learnings=lambda *a, **kw: [],
        _check_and_handle_dedup=lambda *a, **kw: None,
    )

    # A valid write status (not rejected) is the precondition
    assert result.get("status") == "recorded"

    chain = trw_dir / "memory" / "security" / "provenance.jsonl"
    assert chain.exists(), "provenance.jsonl must be written after a learn"
    lines = [line for line in chain.read_text().splitlines() if line.strip()]
    assert len(lines) == 1

    from trw_memory.security.provenance import ProvenanceEntry

    rec = ProvenanceEntry.model_validate_json(lines[0])
    assert rec.learning_id == "L-wire-001"
    assert rec.source_identity == "wiring-test-agent"
    # Signature must be a 128-hex Ed25519 signature (64 bytes)
    assert rec.signature
    assert len(rec.signature) == 128


def test_provenance_chain_verifies_across_two_writes(tmp_path: Path) -> None:
    from trw_memory.security.keys import get_or_create_ed25519_key
    from trw_memory.security.provenance import verify_signed

    from trw_mcp.tools._learn_impl import execute_learn

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    ids = iter(["L-wire-001", "L-wire-002"])

    def gen_id() -> str:
        return next(ids)

    for i in range(2):
        execute_learn(
            summary=f"write {i}",
            detail=f"detail {i}",
            trw_dir=trw_dir,
            config=_make_config(),
            source_identity="wiring-agent",
            _adapter_store=_fake_store,
            _generate_learning_id=gen_id,
            _save_learning_entry=lambda *a, **kw: tmp_path / "entry.yaml",
            _update_analytics=lambda *a, **kw: None,
            _list_active_learnings=lambda *a, **kw: [],
            _check_and_handle_dedup=lambda *a, **kw: None,
        )

    chain = trw_dir / "memory" / "security" / "provenance.jsonl"
    signing_key = get_or_create_ed25519_key(trw_dir)
    assert signing_key is not None, "Ed25519 key must exist after learns"
    broken = verify_signed(chain, signing_key.verify_key)
    assert broken is None, f"chain should verify; first broken link: {broken}"


def test_provenance_wrapper_is_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Provenance is advisory — a failure must not raise."""
    from trw_mcp.tools._learn_impl import _append_provenance_signed

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    # Make the key helper raise; the wrapper must catch it internally.
    def boom(_dir: Path) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr("trw_memory.security.keys.get_or_create_ed25519_key", boom)

    # Must not raise
    _append_provenance_signed(
        trw_dir=trw_dir,
        learning_id="L-x",
        summary="s",
        detail="d",
        source_identity="agent",
    )
