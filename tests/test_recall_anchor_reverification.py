"""CORE268: real anchor refresh belongs to maintenance; recall reports evidence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from trw_memory.models.memory import Anchor, Assertion, AssertionType, MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._maintain_verify import run_maintain_verify
from trw_mcp.tools._recall_impl import _verify_assertions


@pytest.fixture()
def fixture(tmp_path: Path):
    (tmp_path / "mod.py").write_text("def anchored_symbol():\n    return None\n")
    backend = SQLiteBackend(tmp_path / "memory.db")
    backend.store(
        MemoryEntry(
            id="L-anchor",
            content="anchor claim",
            anchor_validity=1.0,
            anchors=[Anchor(file="mod.py", symbol_name="anchored_symbol")],
        )
    )
    yield backend, tmp_path
    backend.close()


def refresh(backend, root):
    return run_maintain_verify(
        backend,
        assertion_failure_penalty=0.15,
        assertion_stale_threshold_days=7,
        anchor_validity_verified_floor=0.8,
        batch_limit=1,
        project_root=root,
    )


def recalled(backend, monkeypatch):
    entry = backend.get("L-anchor", namespace="default").model_dump(mode="json")
    with monkeypatch.context() as guard:

        def forbidden(*args, **kwargs):
            raise AssertionError("recall must not reverify anchors or write")

        guard.setattr("trw_mcp.tools._verification_pass.run_verification_pass", forbidden)
        guard.setattr("trw_mcp.tools._verification_pass.persist_verification_outcome", forbidden)
        result = _verify_assertions([entry], [], TRWConfig(), MagicMock(side_effect=lambda rows, *a, **k: rows))[0]
    assert backend.get("L-anchor", namespace="default").model_dump(mode="json") == entry
    return result


def test_anchor_only_failure_and_correction_are_explicit(fixture, monkeypatch):
    backend, root = fixture
    (root / "mod.py").write_text("def renamed_symbol(): pass\n")
    assert recalled(backend, monkeypatch)["verification_status"] == "unknown"
    refresh(backend, root)
    stale = backend.get("L-anchor", namespace="default")
    assert stale.anchor_validity < 0.8
    assert stale.verification_status != "verified"
    assert recalled(backend, monkeypatch)["verification_evidence"]["current_tree_verified"] is False
    (root / "mod.py").write_text("def anchored_symbol(): pass\n")
    refresh(backend, root)
    assert backend.get("L-anchor", namespace="default").verification_status == "verified"
    assert recalled(backend, monkeypatch)["verification_status"] == "last_known_pass"


def test_intact_anchor_refresh_matches_direct_compute(fixture, monkeypatch):
    from trw_memory.lifecycle.anchor_validation import compute_anchor_validity

    backend, root = fixture
    refresh(backend, root)
    entry = backend.get("L-anchor", namespace="default")
    assert entry.anchor_validity == compute_anchor_validity(entry.anchors, root)
    assert entry.verification_checked_at
    assert recalled(backend, monkeypatch)["verification_status"] == "last_known_pass"


def test_unavailable_anchor_refresh_preserves_prior_evidence(fixture, monkeypatch):
    backend, root = fixture
    refresh(backend, root)
    before = backend.get("L-anchor", namespace="default").model_dump(mode="json")
    refresh(backend, None)
    assert backend.get("L-anchor", namespace="default").model_dump(mode="json") == before
    assert recalled(backend, monkeypatch)["verification_status"] == "last_known_pass"


def test_anchor_and_assertion_refresh_share_one_write(fixture, monkeypatch):
    backend, root = fixture
    backend.update(
        "L-anchor",
        namespace="default",
        assertions=[Assertion(type=AssertionType.GREP_PRESENT, pattern="anchored_symbol", target="mod.py")],
    )
    original = backend.update
    calls = []

    def record(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(backend, "update", record)
    refresh(backend, root)
    assert len(calls) == 1
    assert {"assertions", "anchor_validity", "verification_status", "verification_checked_at"} <= calls[0].keys()
    recalled(backend, monkeypatch)
    assert len(calls) == 1
