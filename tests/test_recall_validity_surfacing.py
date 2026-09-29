"""PRD-CORE-194 FR03 — MCP recall surfaces validity + excludes superseded.

The ``recall_learnings`` path (and the ``_memory_to_learning_dict`` transform)
must (a) exclude superseded records by default and (b) carry a ``superseded``
flag + ``invalidated_by`` on a closed-window record so agents see WHY it is
down-ranked. Open records keep the pre-194 dict shape (no validity keys).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest
from trw_memory.models.memory import MemoryEntry

from tests._memory_store_fake import FakeMemoryStore

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
def trw_dir(tmp_path: Path, fake_memory_store: FakeMemoryStore, monkeypatch: pytest.MonkeyPatch) -> Path:
    trw = tmp_path / ".trw"
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return trw


def _store(fake_memory_store: FakeMemoryStore, entry: MemoryEntry) -> None:
    """Write straight into the fake's rows dict: bi-temporal fields (valid_from/
    invalid_from/invalidated_by) have no public store-API equivalent."""
    fake_memory_store.rows[(entry.namespace, entry.id)] = entry


def test_recall_learnings_excludes_superseded_by_default(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    from trw_mcp.state._memory_recall import recall_learnings

    _store(
        fake_memory_store,
        MemoryEntry(
            id="L-aaaa",
            content="git rollback stash workflow",
            namespace="default",
            created_at=T0,
            valid_from=T0,
            invalid_from=T2,
            invalidated_by="L-bbbb",
        ),
    )
    _store(
        fake_memory_store,
        MemoryEntry(
            id="L-bbbb",
            content="git rollback stash workflow",
            namespace="default",
            created_at=T2,
            valid_from=T2,
        ),
    )

    results = recall_learnings(trw_dir, "git rollback stash", max_results=10)
    ids = {r["id"] for r in results}
    assert "L-bbbb" in ids
    assert "L-aaaa" not in ids


# ---------------------------------------------------------------------------
# PRD-CORE-194 FR03 — MCP recall as_of / include_superseded surface (NEW-1)
# ---------------------------------------------------------------------------


def _store_superseded_pair(fake_memory_store: FakeMemoryStore) -> None:
    """A superseded record L-aaaa (window [T0,T2)) replaced by open L-bbbb."""
    _store(
        fake_memory_store,
        MemoryEntry(
            id="L-aaaa",
            content="git rollback stash workflow",
            namespace="default",
            created_at=T0,
            valid_from=T0,
            invalid_from=T2,
            invalidated_by="L-bbbb",
        ),
    )
    _store(
        fake_memory_store,
        MemoryEntry(
            id="L-bbbb",
            content="git rollback stash workflow",
            namespace="default",
            created_at=T2,
            valid_from=T2,
        ),
    )


def test_recall_as_of_none_default_is_unchanged(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    """as_of=None (the default) excludes the superseded record — bit-identical."""
    from trw_mcp.state._memory_recall import recall_learnings

    _store_superseded_pair(fake_memory_store)

    explicit_default = recall_learnings(trw_dir, "git rollback stash", max_results=10, as_of=None)
    implicit_default = recall_learnings(trw_dir, "git rollback stash", max_results=10)

    assert {r["id"] for r in explicit_default} == {r["id"] for r in implicit_default}
    assert "L-aaaa" not in {r["id"] for r in explicit_default}
    assert "L-bbbb" in {r["id"] for r in explicit_default}


def test_recall_as_of_reincludes_record_superseded_after_that_time(
    trw_dir: Path, fake_memory_store: FakeMemoryStore
) -> None:
    """as_of=T1 (inside [T0,T2)): the superseded record is eligible; the open one
    (opens at T2) is excluded because its window had not begun."""
    from trw_mcp.state._memory_recall import recall_learnings

    _store_superseded_pair(fake_memory_store)

    results = recall_learnings(trw_dir, "git rollback stash", max_results=10, as_of=T1.isoformat())
    ids = {r["id"] for r in results}
    assert "L-aaaa" in ids
    assert "L-bbbb" not in ids


def test_recall_as_of_accepts_trailing_z(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    """A trailing 'Z' (UTC) is accepted, matching the +00:00 form."""
    from trw_mcp.state._memory_recall import recall_learnings

    _store_superseded_pair(fake_memory_store)

    results = recall_learnings(trw_dir, "git rollback stash", max_results=10, as_of="2026-01-02T00:00:00Z")
    assert "L-aaaa" in {r["id"] for r in results}


def test_recall_malformed_as_of_raises_clean_value_error(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    """A malformed as_of surfaces a ValueError (clean validation error), not a
    bare traceback from deep inside datetime parsing."""
    from trw_mcp.state._memory_recall import recall_learnings

    _store_superseded_pair(fake_memory_store)

    with pytest.raises(ValueError, match="ISO-8601"):
        recall_learnings(trw_dir, "git rollback stash", max_results=10, as_of="not-a-date")


def test_recall_include_superseded_surfaces_with_flags(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    """include_superseded=True returns the superseded record (ranked below the open
    one) carrying its superseded / invalidated_by flags from the transform."""
    from trw_mcp.state._memory_recall import recall_learnings

    _store_superseded_pair(fake_memory_store)

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
        _rank_by_utility=lambda items, *a, **k: list(items),
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
        _rank_by_utility=lambda items, *a, **k: list(items),
    )
    assert "as_of" not in captured
    assert "include_superseded" not in captured


# ---------------------------------------------------------------------------
# PRD-CORE-326: provenance keys on the default trw_recall stub, asserted on
# what the served entrypoint (execute_recall) returns.
# ---------------------------------------------------------------------------


def _served_stubs(rows: list[dict[str, object]], *, home: str = "project:test") -> list[dict[str, object]]:
    """The stubs ``execute_recall`` returns when its store yields exactly *rows*, in order.

    *home* is the checkout's pinned ``project_namespace``.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import execute_recall

    result = execute_recall(
        query="widget",
        trw_dir=Path("/nonexistent"),
        config=get_config().model_copy(update={"project_namespace": home}),
        track=False,
        _adapter_recall=lambda *_a, **_k: [dict(row) for row in rows],
        _rank_by_utility=lambda items, *a, **k: list(items),
    )
    return cast("list[dict[str, object]]", result["learnings"])


_PROVENANCE_KEYS = {"source", "scope", "superseded_by"}


@pytest.mark.parametrize("source_type", ["human", "tool", "consolidated", "team_sync", "company_sync"])
def test_non_default_source_is_surfaced(source_type: str) -> None:
    """FR01: a non-default stored source is read off the default stub, with no ids=[...] call."""
    [served] = _served_stubs([{"id": "L-1", "summary": "widget rule", "source_type": source_type}])

    assert served == {"id": "L-1", "claim": "widget rule", "source": source_type}


@pytest.mark.parametrize("row_extra", [{"source_type": "agent"}, {}], ids=["agent", "missing"])
def test_default_source_carries_no_source_key(row_extra: dict[str, object]) -> None:
    """FR01: the stored default ``agent`` (or no key at all) adds no byte to the stub."""
    [served] = _served_stubs([{"id": "L-1", "summary": "widget rule", **row_extra}])

    assert served == {"id": "L-1", "claim": "widget rule"}


def test_remote_row_carries_no_provenance_keys() -> None:
    """FR01/Non-Goals: a shared row's peer-asserted fields are never presented as provenance."""
    remote = {
        "id": "R-1",
        "summary": "[shared] widget rule",
        "source": "shared",
        "source_type": "human",
        "namespace": "team:core",
        "superseded": True,
        "invalidated_by": "L-9f8e7d6c",
    }
    [served] = _served_stubs([remote])

    assert served == {"id": "R-1", "claim": "[shared] widget rule"}


def test_legacy_row_renders_without_provenance_keys() -> None:
    """NFR03: a row missing every read key renders, and carries none of the three keys."""
    [served] = _served_stubs([{"id": "L-1", "summary": "widget rule"}])

    assert _PROVENANCE_KEYS.isdisjoint(served)


def test_provenance_stub_does_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR02: the stub reads only keys already on the row: no file, socket or subprocess."""
    import builtins
    import socket
    import subprocess

    from trw_mcp.tools._recall_presenter import stub

    def _refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("stub(provenance=True) performed I/O")

    for owner, name in ((builtins, "open"), (socket, "socket"), (subprocess, "Popen")):
        monkeypatch.setattr(owner, name, _refuse)
    row = {"id": "L-1", "summary": "s", "source_type": "human", "namespace": "team:core", "superseded": True}

    assert stub({**row, "invalidated_by": "L-2"}, provenance=True)["source"] == "human"


def test_non_default_namespace_is_surfaced(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    """FR02, end to end: a user-tier row names its scope; a row in ``default`` does not."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._tier_routing import USER_NAMESPACE
    from trw_mcp.tools._recall_impl import execute_recall

    _store(fake_memory_store, MemoryEntry(id="L-user", content="widget user rule", namespace=USER_NAMESPACE))
    _store(fake_memory_store, MemoryEntry(id="L-proj", content="widget project rule", namespace="default"))

    result = execute_recall("widget", trw_dir, get_config().model_copy(update={"project_namespace": "project:test"}))
    served = {row["id"]: row for row in cast("list[dict[str, object]]", result["learnings"])}

    assert served["L-user"].get("scope") == USER_NAMESPACE
    assert "scope" not in served["L-proj"]


@pytest.mark.parametrize(
    ("namespace", "scope"),
    [("team:core", "team:core"), ("project:test", None), ("default", None), (None, None)],
    ids=["other", "home", "default", "missing"],
)
def test_scope_is_omitted_for_the_home_and_default_namespaces(namespace: str | None, scope: str | None) -> None:
    """FR02: only a namespace other than ``default`` and the checkout's own carries signal."""
    row: dict[str, object] = {"id": "L-1", "summary": "widget rule"}
    if namespace is not None:
        row["namespace"] = namespace
    [served] = _served_stubs([row], home="project:test")

    assert served.get("scope") == scope


@pytest.mark.parametrize("namespace", ["t" * 40, "\u00e9" * 40, '"' * 40], ids=["ascii", "non-ascii", "quotes"])
def test_scope_is_bounded_in_rendered_bytes(namespace: str) -> None:
    """FR02 bound: an over-cap value is cut to 32 escaped bytes and marked with ``...``."""
    import json

    [served] = _served_stubs([{"id": "L-1", "summary": "widget rule", "namespace": namespace}])
    scope = str(served.get("scope", ""))

    assert scope.endswith("...")
    assert namespace.startswith(scope[:-3])
    assert len(json.dumps(scope)) - 2 <= 32


def test_recall_by_ids_row_carries_its_non_default_namespace(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    """FR02: the full row an agent fetches next names the same scope; ``default`` stays unprinted."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._tier_routing import USER_NAMESPACE
    from trw_mcp.tools._recall_impl import recall_by_ids

    _store(fake_memory_store, MemoryEntry(id="L-user", content="widget user rule", namespace=USER_NAMESPACE))
    _store(fake_memory_store, MemoryEntry(id="L-proj", content="widget project rule", namespace="default"))

    rows = {row["id"]: row for row in recall_by_ids(trw_dir, get_config(), ["L-user", "L-proj"])["learnings"]}

    assert rows["L-user"].get("namespace") == USER_NAMESPACE
    assert "namespace" not in rows["L-proj"]


def test_superseded_row_names_its_closer(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    """FR04, end to end: under include_superseded the closed-window row names its closer."""
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import execute_recall

    # Distinct wording: recall collapses exact-content duplicates before presenting.
    closed = MemoryEntry(
        id="L-aaaa", content="git stash old rule", valid_from=T0, invalid_from=T2, invalidated_by="L-bbbb"
    )
    _store(fake_memory_store, closed)
    _store(fake_memory_store, MemoryEntry(id="L-bbbb", content="git stash new rule", valid_from=T2))

    result = execute_recall("git stash", trw_dir, get_config(), include_superseded=True)
    served = {row["id"]: row for row in cast("list[dict[str, object]]", result["learnings"])}

    assert served["L-aaaa"].get("superseded_by") == "L-bbbb"
    assert "superseded_by" not in served["L-bbbb"]


@pytest.mark.parametrize(
    "row_extra",
    [{"status": "obsolete"}, {"superseded": False, "invalidated_by": "L-2"}, {"superseded": True}],
    ids=["obsolete-open-window", "open-window", "no-closer"],
)
def test_open_window_row_names_no_closer(row_extra: dict[str, object]) -> None:
    """FR04/NFR03: a retired-by-status or open-window row, or one missing its closer, gets no key."""
    [served] = _served_stubs([{"id": "L-1", "summary": "widget rule", **row_extra}])

    assert "superseded_by" not in served


def test_superseded_by_is_bounded_in_rendered_bytes() -> None:
    """FR04 bound: a caller-supplied closer id is cut to 32 escaped bytes."""
    import json

    closer = "L-" + "\u00e9" * 40
    [served] = _served_stubs([{"id": "L-1", "summary": "widget rule", "superseded": True, "invalidated_by": closer}])
    shown = str(served.get("superseded_by", ""))

    assert shown.endswith("...")
    assert closer.startswith(shown[:-3])
    assert len(json.dumps(shown)) - 2 <= 32
