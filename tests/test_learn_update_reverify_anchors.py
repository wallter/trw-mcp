"""PRD-CORE-231-FR03: ``trw_learn_update(reverify_anchors=True)``.

PRD-CORE-111-FR03 mandated ``compute_anchor_validity()`` be callable from
``trw_learn_update()`` re-verification; the parameter never existed. These tests
drive the REAL registered MCP tool against a real backend.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest
from trw_memory.models.memory import Anchor, MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from tests.conftest import extract_tool_fn, make_test_server

_ANCHORED_SOURCE = "def anchored_symbol() -> None:\n    return None\n"


@pytest.fixture()
def project(tmp_project: Path) -> Path:
    (tmp_project / "src").mkdir()
    (tmp_project / "src" / "mod.py").write_text(_ANCHORED_SOURCE, encoding="utf-8")
    return tmp_project


@pytest.fixture()
def backend(project: Path) -> SQLiteBackend:
    return SQLiteBackend(project / ".trw" / "memory.db")


def _wire(monkeypatch: pytest.MonkeyPatch, backend: SQLiteBackend, project: Path) -> None:
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: project / ".trw")
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_project_root", lambda: project)
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: project / ".trw")
    monkeypatch.setattr("trw_mcp.state.memory_adapter.get_backend", lambda _trw_dir: backend)


def _anchor() -> Anchor:
    return Anchor(file="src/mod.py", symbol_name="anchored_symbol", symbol_type="function")


def _store(backend: SQLiteBackend, entry_id: str, *, anchors: list[Anchor]) -> None:
    now = datetime.now(timezone.utc)
    backend.store(
        MemoryEntry(
            id=entry_id,
            content="anchored claim",
            created_at=now,
            updated_at=now,
            anchors=anchors,
            anchor_validity=1.0,
        )
    )


def _update_fn():  # type: ignore[no-untyped-def]
    return extract_tool_fn(make_test_server("learning"), "trw_learn_update")


def test_parameter_is_exposed_and_defaults_off() -> None:
    """Backward compatible: existing callers see no behavior change."""
    signature = inspect.signature(_update_fn())
    assert "reverify_anchors" in signature.parameters
    assert signature.parameters["reverify_anchors"].default is False


def test_reverify_updates_score(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """The freshly computed score replaces the write-time one in storage."""
    _wire(monkeypatch, backend, project)
    _store(backend, "L-upd", anchors=[_anchor()])
    (project / "src" / "mod.py").unlink()

    _update_fn()(learning_id="L-upd", reverify_anchors=True)

    entry = backend.get("L-upd")
    assert entry is not None
    assert entry.anchor_validity == 0.0


def test_default_leaves_the_write_time_score_alone(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """Without the flag, a routine field edit never recomputes anchors."""
    _wire(monkeypatch, backend, project)
    _store(backend, "L-noflag", anchors=[_anchor()])
    (project / "src" / "mod.py").unlink()

    _update_fn()(learning_id="L-noflag", status="resolved")

    entry = backend.get("L-noflag")
    assert entry is not None
    assert entry.anchor_validity == 1.0


def test_reverify_runs_before_other_field_updates(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """FR03 ordering: the refreshed score persists AND the field edit applies."""
    _wire(monkeypatch, backend, project)
    _store(backend, "L-both", anchors=[_anchor()])
    (project / "src" / "mod.py").unlink()

    _update_fn()(learning_id="L-both", detail="sharpened detail", reverify_anchors=True)

    entry = backend.get("L-both")
    assert entry is not None
    assert entry.anchor_validity == 0.0
    assert entry.detail == "sharpened detail"


def test_entry_without_anchors_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """No anchors => nothing to re-verify; validity stays at its 1.0 default."""
    _wire(monkeypatch, backend, project)
    _store(backend, "L-bare", anchors=[])

    result = _update_fn()(learning_id="L-bare", reverify_anchors=True, status="resolved")

    assert result["status"] == "updated"
    entry = backend.get("L-bare")
    assert entry is not None
    assert entry.anchor_validity == 1.0


def test_intact_anchor_is_not_demoted(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """No false demotion when the anchored symbol is still present."""
    _wire(monkeypatch, backend, project)
    _store(backend, "L-ok", anchors=[_anchor()])

    _update_fn()(learning_id="L-ok", reverify_anchors=True)

    entry = backend.get("L-ok")
    assert entry is not None
    assert entry.anchor_validity == 1.0
