"""PRD-CORE-194 FR04 — trw_learn_update supersession branch.

Coordinator OQ4 resolution: the supersession branch fires ONLY on an explicit
``supersedes=<record_id>`` parameter, NEVER on a routine field edit. Updating
learning B with ``supersedes=A`` closes A's validity window (invalid_from +
invalidated_by=B) and retains A (no delete). A plain field edit (no supersedes)
leaves every prior record's window open.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend


def _learn_update_fn() -> object:
    from fastmcp import FastMCP

    from trw_mcp.tools.learning import register_learning_tools

    server = FastMCP("test")
    register_learning_tools(server)

    async def _get() -> object:
        for t in await server.list_tools():
            if t.name == "trw_learn_update":
                return t.fn
        raise KeyError("trw_learn_update not found")

    return asyncio.run(_get())


@pytest.fixture()
def trw_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    trw = tmp_path / ".trw"
    (trw / "learnings" / "entries").mkdir(parents=True)
    (trw / "memory").mkdir(parents=True)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    # Reset the memory backend singleton so the test's writes land in this tmp store.
    from trw_mcp.state.memory_adapter import reset_backend

    reset_backend()
    return tmp_path


def _seed(trw_dir: Path, *ids: str) -> SQLiteBackend:
    from trw_mcp.state.memory_adapter import get_backend

    backend = get_backend(trw_dir)
    for i in ids:
        backend.store(MemoryEntry(id=i, content=f"content {i}", namespace="default"))
    return backend


def test_learn_update_supersedes(trw_project: Path) -> None:
    """supersedes=A closes A's window with invalidated_by=B; A is retained."""
    trw_dir = trw_project / ".trw"
    backend = _seed(trw_dir, "L-aaaa", "L-bbbb")
    fn = _learn_update_fn()

    # Update B (L-bbbb) declaring it supersedes A (L-aaaa).
    result = fn(learning_id="L-bbbb", supersedes="L-aaaa", summary="corrected fact")

    assert result["status"] == "updated"

    a = backend.get("L-aaaa")
    assert a is not None
    assert a.invalid_from is not None  # window closed
    assert a.invalidated_by == "L-bbbb"  # closer is the updating record
    assert a.validity_state() == "superseded"
    # Retained (not deleted) — still gettable.
    b = backend.get("L-bbbb")
    assert b is not None
    assert b.invalid_from is None  # the superseding record stays open


def test_plain_edit_does_not_supersede(trw_project: Path) -> None:
    """OQ4: a routine field edit (no supersedes=) closes no window."""
    trw_dir = trw_project / ".trw"
    backend = _seed(trw_dir, "L-cccc")
    fn = _learn_update_fn()

    result = fn(learning_id="L-cccc", detail="sharper detail")
    assert result["status"] == "updated"

    c = backend.get("L-cccc")
    assert c is not None
    assert c.invalid_from is None
    assert c.invalidated_by is None
    assert c.validity_state() == "open"


def test_supersedes_missing_prior_is_reported(trw_project: Path) -> None:
    """supersedes=<unknown id> does not crash; the edit still applies."""
    trw_dir = trw_project / ".trw"
    backend = _seed(trw_dir, "L-dddd")
    fn = _learn_update_fn()

    result = fn(learning_id="L-dddd", supersedes="L-nope", summary="x")
    # The primary update still succeeds; the missing prior is a no-op close.
    assert result["status"] == "updated"
    d = backend.get("L-dddd")
    assert d is not None
    assert d.invalid_from is None
