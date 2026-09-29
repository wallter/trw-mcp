"""Stand-ins for ``DaemonClient`` around the anchored lookup (PRD-CORE-332 S3).

``TextOnlyDaemon`` is a client from before PRD-CORE-332 S2: it serves ``memory_recall``
and has no ``anchored`` method. ``AnchoredDaemon`` adds ``anchored`` with the S2
contract: ``memory_anchored(namespace, file, limit, status)`` answering
``{"status": "ok", "memories": [...]}`` in ``memory_recall``'s row shape, ordered as
the daemon ranks (importance, then recency). Both record every tool they serve.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from trw_memory.models.memory import Anchor, MemoryEntry

from trw_mcp.state import _store_selection
from trw_mcp.state._daemon_store import DaemonMemoryStore

NAMESPACE = "project:anchor-test"


def lesson(entry_id: str, content: str, *, anchors: tuple[str, ...] = (), **fields: Any) -> MemoryEntry:
    """A project row whose anchors name *anchors*; *content* is the only searchable text."""
    return MemoryEntry(
        id=entry_id,
        content=content,
        namespace=fields.pop("namespace", NAMESPACE),
        anchors=[Anchor(file=file, symbol_name="thing") for file in anchors],
        **fields,
    )


def _rows(rows: list[MemoryEntry]) -> dict[str, Any]:
    return {"status": "ok", "memories": [row.model_dump(mode="json") for row in rows]}


class TextOnlyDaemon:
    """``memory_recall`` by substring over content; no ``anchored`` attribute."""

    def __init__(self, rows: list[MemoryEntry]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str]] = []

    async def recall(self, query: str, namespace: str, *, limit: int, status: str | None = None, **_: Any) -> Any:
        self.calls.append(("memory_recall", namespace))
        needle = query.lower()
        hits = [r for r in self.rows if r.namespace == namespace and needle in r.content.lower()]
        return _rows([r for r in hits if status is None or r.status == status][:limit])

    async def list_page(self, namespace: str, size: int, after: object, **_: Any) -> Any:
        self.calls.append(("memory_list_page", namespace))
        return {"status": "ok", "entries": [], "next": None}

    async def get(self, memory_id: str, namespace: str) -> Any:
        found = [r for r in self.rows if r.id == memory_id and r.namespace == namespace]
        return {"status": "ok", "entry": found[0].model_dump(mode="json")} if found else {"status": "not_found"}


class AnchoredDaemon(TextOnlyDaemon):
    """A daemon serving ``memory_anchored``, or refusing it with *error* when given."""

    def __init__(self, rows: list[MemoryEntry], *, error: Exception | None = None) -> None:
        super().__init__(rows)
        self.error = error
        self.files: list[str] = []

    async def anchored(self, namespace: str, file: str, limit: int, status: str | None = None) -> Any:
        self.calls.append(("memory_anchored", namespace))
        self.files.append(file)
        if self.error is not None:
            raise self.error
        hits = [
            r
            for r in self.rows
            if r.namespace == namespace
            and any(a.file == file for a in r.anchors)
            and (status is None or r.status == status)
        ]
        hits.sort(key=lambda r: (-r.importance, -r.updated_at.timestamp(), r.id))
        return _rows(hits[:limit])


def use_daemon(monkeypatch: pytest.MonkeyPatch, project: Path, client: object) -> None:
    """Route this checkout's recall to a ``DaemonMemoryStore`` over *client*."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    (project / ".trw").mkdir(parents=True, exist_ok=True)
    store = DaemonMemoryStore(client, NAMESPACE)  # type: ignore[arg-type]
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, NAMESPACE))
