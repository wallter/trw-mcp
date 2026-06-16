"""PRD-CORE-194 FR03 — MCP recall surfaces validity + excludes superseded.

The ``recall_learnings`` path (and the ``_memory_to_learning_dict`` transform)
must (a) exclude superseded records by default and (b) carry a ``superseded``
flag + ``invalidated_by`` on a closed-window record so agents see WHY it is
down-ranked. Open records keep the pre-194 dict shape (no validity keys).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from trw_memory.models.memory import MemoryEntry

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 2, tzinfo=timezone.utc)
T2 = datetime(2026, 1, 3, tzinfo=timezone.utc)


def test_transform_surfaces_superseded_flag() -> None:
    from trw_mcp.state._memory_transforms import _memory_to_learning_dict

    closed = MemoryEntry(
        id="L-closed",
        content="c",
        created_at=T0,
        valid_from=T0,
        invalid_from=T2,
        invalidated_by="L-new",
    )
    d = _memory_to_learning_dict(closed)
    assert d["superseded"] is True
    assert d["invalidated_by"] == "L-new"


def test_transform_open_entry_has_no_validity_keys() -> None:
    from trw_mcp.state._memory_transforms import _memory_to_learning_dict

    open_entry = MemoryEntry(id="L-open", content="c", created_at=T0, valid_from=T0)
    d = _memory_to_learning_dict(open_entry)
    assert "superseded" not in d
    assert "invalidated_by" not in d


@pytest.fixture()
def trw_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    trw = tmp_path / ".trw"
    (trw / "memory").mkdir(parents=True)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    from trw_mcp.state.memory_adapter import reset_backend

    reset_backend()
    return trw


def test_recall_learnings_excludes_superseded_by_default(trw_dir: Path) -> None:
    from trw_mcp.state._memory_recall import recall_learnings
    from trw_mcp.state.memory_adapter import get_backend

    backend = get_backend(trw_dir)
    backend.store(
        MemoryEntry(
            id="L-aaaa",
            content="git rollback stash workflow",
            namespace="default",
            created_at=T0,
            valid_from=T0,
            invalid_from=T2,
            invalidated_by="L-bbbb",
        )
    )
    backend.store(
        MemoryEntry(
            id="L-bbbb",
            content="git rollback stash workflow",
            namespace="default",
            created_at=T2,
            valid_from=T2,
        )
    )

    results = recall_learnings(trw_dir, "git rollback stash", max_results=10)
    ids = {r["id"] for r in results}
    assert "L-bbbb" in ids
    assert "L-aaaa" not in ids


# ---------------------------------------------------------------------------
# PRD-CORE-194 FR03 — MCP recall as_of / include_superseded surface (NEW-1)
# ---------------------------------------------------------------------------


def _store_superseded_pair(backend: object) -> None:
    """A superseded record L-aaaa (window [T0,T2)) replaced by open L-bbbb."""
    backend.store(  # type: ignore[attr-defined]
        MemoryEntry(
            id="L-aaaa",
            content="git rollback stash workflow",
            namespace="default",
            created_at=T0,
            valid_from=T0,
            invalid_from=T2,
            invalidated_by="L-bbbb",
        )
    )
    backend.store(  # type: ignore[attr-defined]
        MemoryEntry(
            id="L-bbbb",
            content="git rollback stash workflow",
            namespace="default",
            created_at=T2,
            valid_from=T2,
        )
    )


def test_recall_as_of_none_default_is_unchanged(trw_dir: Path) -> None:
    """as_of=None (the default) excludes the superseded record — bit-identical."""
    from trw_mcp.state._memory_recall import recall_learnings
    from trw_mcp.state.memory_adapter import get_backend

    _store_superseded_pair(get_backend(trw_dir))

    explicit_default = recall_learnings(trw_dir, "git rollback stash", max_results=10, as_of=None)
    implicit_default = recall_learnings(trw_dir, "git rollback stash", max_results=10)

    assert {r["id"] for r in explicit_default} == {r["id"] for r in implicit_default}
    assert "L-aaaa" not in {r["id"] for r in explicit_default}
    assert "L-bbbb" in {r["id"] for r in explicit_default}


def test_recall_as_of_reincludes_record_superseded_after_that_time(trw_dir: Path) -> None:
    """as_of=T1 (inside [T0,T2)): the superseded record is eligible; the open one
    (opens at T2) is excluded because its window had not begun."""
    from trw_mcp.state._memory_recall import recall_learnings
    from trw_mcp.state.memory_adapter import get_backend

    _store_superseded_pair(get_backend(trw_dir))

    results = recall_learnings(trw_dir, "git rollback stash", max_results=10, as_of=T1.isoformat())
    ids = {r["id"] for r in results}
    assert "L-aaaa" in ids
    assert "L-bbbb" not in ids


def test_recall_as_of_accepts_trailing_z(trw_dir: Path) -> None:
    """A trailing 'Z' (UTC) is accepted, matching the +00:00 form."""
    from trw_mcp.state._memory_recall import recall_learnings
    from trw_mcp.state.memory_adapter import get_backend

    _store_superseded_pair(get_backend(trw_dir))

    results = recall_learnings(trw_dir, "git rollback stash", max_results=10, as_of="2026-01-02T00:00:00Z")
    assert "L-aaaa" in {r["id"] for r in results}


def test_recall_malformed_as_of_raises_clean_value_error(trw_dir: Path) -> None:
    """A malformed as_of surfaces a ValueError (clean validation error), not a
    bare traceback from deep inside datetime parsing."""
    from trw_mcp.state._memory_recall import recall_learnings
    from trw_mcp.state.memory_adapter import get_backend

    _store_superseded_pair(get_backend(trw_dir))

    with pytest.raises(ValueError, match="ISO-8601"):
        recall_learnings(trw_dir, "git rollback stash", max_results=10, as_of="not-a-date")


def test_recall_include_superseded_surfaces_with_flags(trw_dir: Path) -> None:
    """include_superseded=True returns the superseded record (ranked below the open
    one) carrying its superseded / invalidated_by flags from the transform."""
    from trw_mcp.state._memory_recall import recall_learnings
    from trw_mcp.state.memory_adapter import get_backend

    _store_superseded_pair(get_backend(trw_dir))

    results = recall_learnings(trw_dir, "git rollback stash", max_results=10, include_superseded=True)
    by_id = {r["id"]: r for r in results}
    assert "L-aaaa" in by_id
    assert "L-bbbb" in by_id
    superseded = by_id["L-aaaa"]
    assert superseded["superseded"] is True
    assert superseded["invalidated_by"] == "L-bbbb"
    # The open record keeps the pre-194 dict shape (no validity keys).
    assert "superseded" not in by_id["L-bbbb"]


def test_execute_recall_forwards_as_of_and_include_superseded() -> None:
    """execute_recall forwards as_of/include_superseded into the recall kwargs
    only when set (default-omitted keeps injected recall doubles back-compatible)."""
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import execute_recall

    captured: dict[str, object] = {}

    def _fake_recall(trw_dir: Path, **kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return []

    execute_recall(
        query="anything",
        trw_dir=Path("/nonexistent"),
        config=get_config(),
        as_of="2026-01-02T00:00:00Z",
        include_superseded=True,
        _adapter_recall=_fake_recall,
        _adapter_update_access=lambda *a, **k: None,
        _search_patterns=lambda *a, **k: [],
        _rank_by_utility=lambda items, *a, **k: list(items),
        _collect_context=lambda *a, **k: {},
    )
    assert captured["as_of"] == "2026-01-02T00:00:00Z"
    assert captured["include_superseded"] is True


def test_execute_recall_omits_validity_kwargs_by_default() -> None:
    """Defaults are NOT forwarded, so a recall double without the params stays
    back-compatible (byte-identical pre-194 call shape)."""
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import execute_recall

    captured: dict[str, object] = {}

    def _fake_recall(trw_dir: Path, **kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return []

    execute_recall(
        query="anything",
        trw_dir=Path("/nonexistent"),
        config=get_config(),
        _adapter_recall=_fake_recall,
        _adapter_update_access=lambda *a, **k: None,
        _search_patterns=lambda *a, **k: [],
        _rank_by_utility=lambda items, *a, **k: list(items),
        _collect_context=lambda *a, **k: {},
    )
    assert "as_of" not in captured
    assert "include_superseded" not in captured
