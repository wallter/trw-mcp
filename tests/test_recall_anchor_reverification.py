"""PRD-CORE-231-FR03: recall re-verifies anchors instead of trusting write time.

``compute_anchor_validity()`` used to have exactly one call site — ``trw_learn``'s
write path — so ``anchor_validity`` froze at its creation value (usually 1.0) and
kept boosting recall ranking after the anchored symbol was renamed or deleted.
These tests exercise the real recall verification pass against a real backend.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from trw_memory.models.memory import Anchor, Assertion, AssertionType, MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import TRWConfig

_ANCHORED_SOURCE = "def anchored_symbol() -> None:\n    return None\n"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text(_ANCHORED_SOURCE, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def backend(tmp_path: Path) -> SQLiteBackend:
    return SQLiteBackend(tmp_path / "store" / "memory.db")


def _wire(monkeypatch: pytest.MonkeyPatch, backend: SQLiteBackend, project: Path) -> None:
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: project / ".trw")
    monkeypatch.setattr("trw_mcp.state.memory_adapter.get_backend", lambda _trw_dir: backend)


def _anchor() -> Anchor:
    return Anchor(file="src/mod.py", symbol_name="anchored_symbol", symbol_type="function")


def _store_anchored(backend: SQLiteBackend, entry_id: str, *, assertions: list[Assertion] | None = None) -> None:
    now = datetime.now(timezone.utc)
    backend.store(
        MemoryEntry(
            id=entry_id,
            content="anchored claim",
            created_at=now,
            updated_at=now,
            anchors=[_anchor()],
            # Write-time score, as trw_learn would have recorded it.
            anchor_validity=1.0,
            assertions=assertions or [],
        )
    )


def _learning(entry_id: str, *, assertions: list[Assertion] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": entry_id,
        "summary": "anchored claim",
        "anchors": [_anchor().model_dump(mode="json")],
        "anchor_validity": 1.0,
    }
    if assertions:
        payload["assertions"] = [a.model_dump(mode="json") for a in assertions]
    return payload


def _rank(entries: list[dict[str, object]], *_args: Any, **_kwargs: Any) -> list[dict[str, object]]:
    return entries


def test_recall_persists_anchor_validity(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """Deleting the anchored file demotes the PERSISTED score, not just the payload."""
    from trw_mcp.tools._recall_impl import _verify_assertions

    _wire(monkeypatch, backend, project)
    _store_anchored(backend, "L-anchor")
    assert backend.get("L-anchor") is not None
    assert backend.get("L-anchor").anchor_validity == 1.0  # type: ignore[union-attr]

    # The anchored file goes away — the classic "memory was for the line above".
    (project / "src" / "mod.py").unlink()

    result = _verify_assertions([_learning("L-anchor")], ["q"], TRWConfig(), _rank)

    assert result[0]["anchor_validity"] == 0.0
    persisted = backend.get("L-anchor")
    assert persisted is not None
    assert persisted.anchor_validity == 0.0


def test_recall_reverifies_entries_without_assertions(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """An anchored entry with NO assertions must still be re-verified."""
    from trw_mcp.tools._recall_impl import _verify_assertions

    _wire(monkeypatch, backend, project)
    _store_anchored(backend, "L-anchor-only")
    (project / "src" / "mod.py").write_text("def renamed_symbol() -> None:\n    return None\n", encoding="utf-8")

    _verify_assertions([_learning("L-anchor-only")], ["q"], TRWConfig(), _rank)

    persisted = backend.get("L-anchor-only")
    assert persisted is not None
    assert persisted.anchor_validity == 0.0


def test_intact_anchor_keeps_full_validity(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """No false demotion: an anchor that still resolves stays at 1.0."""
    from trw_mcp.tools._recall_impl import _verify_assertions

    _wire(monkeypatch, backend, project)
    _store_anchored(backend, "L-intact")

    _verify_assertions([_learning("L-intact")], ["q"], TRWConfig(), _rank)

    persisted = backend.get("L-intact")
    assert persisted is not None
    assert persisted.anchor_validity == 1.0


def test_recall_call_site_matches_a_direct_compute(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """The new call site introduces no scoring drift versus a direct call."""
    from trw_memory.lifecycle.anchor_validation import compute_anchor_validity

    from trw_mcp.tools._recall_impl import _verify_assertions

    _wire(monkeypatch, backend, project)
    _store_anchored(backend, "L-parity")
    (project / "src" / "mod.py").write_text("def renamed_symbol() -> None:\n    return None\n", encoding="utf-8")

    direct = compute_anchor_validity([_anchor().model_dump(mode="json")], str(project), learning_id="L-parity")
    result = _verify_assertions([_learning("L-parity")], ["q"], TRWConfig(), _rank)

    assert result[0]["anchor_validity"] == direct


def test_unanchored_entry_is_untouched(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """Regression guard: entries with no anchors keep the pre-FR03 behavior."""
    from trw_mcp.tools._recall_impl import _verify_assertions

    _wire(monkeypatch, backend, project)
    now = datetime.now(timezone.utc)
    backend.store(MemoryEntry(id="L-plain", content="unanchored", created_at=now, updated_at=now))

    result = _verify_assertions([{"id": "L-plain", "summary": "unanchored"}], ["q"], TRWConfig(), _rank)

    assert "anchor_validity" not in result[0]
    persisted = backend.get("L-plain")
    assert persisted is not None
    assert persisted.anchor_validity == 1.0


def test_anchor_and_assertion_share_one_write(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """FR03: the refreshed score rides the SAME batched update as FR02's verdict."""
    from trw_mcp.tools._recall_impl import _verify_assertions

    _wire(monkeypatch, backend, project)
    assertion = Assertion(type=AssertionType.GREP_PRESENT, pattern="anchored_symbol", target="**/*.py")
    _store_anchored(backend, "L-both", assertions=[assertion])
    (project / "src" / "mod.py").unlink()

    calls: list[dict[str, object]] = []

    class _RecordingBackend:
        def update(self, entry_id: str, **fields: object) -> MemoryEntry | None:
            calls.append(fields)
            return backend.update(entry_id, **fields)

    monkeypatch.setattr("trw_mcp.state.memory_adapter.get_backend", lambda _trw_dir: _RecordingBackend())

    _verify_assertions([_learning("L-both", assertions=[assertion])], ["q"], TRWConfig(), _rank)

    assert len(calls) == 1
    assert set(calls[0]) >= {"assertions", "verification_status", "anchor_validity"}
    assert calls[0]["anchor_validity"] == 0.0
