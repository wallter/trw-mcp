"""The client reads a verify summary from a daemon of a later minor version (PRD-CORE-362 follow-up).

The daemon refuses a client of another MAJOR version only, so a newer daemon of the same major can answer an
older client. When a summary gains a count, the older client must keep working with the counts it knows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class _Client:
    """A daemon client whose ``verify`` answers in two slices, with one count this client does not know."""

    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, namespace: str, root: str | None, settings: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        summary = {"entries_processed": 2, "stale_transitions": 1, "a_count_from_a_later_minor": 5}
        return {"status": "ok", "summary": summary, "next": ["L-2", "x"] if self.calls == 1 else None}


def test_verify_keeps_the_counts_it_knows_when_the_daemon_sends_one_it_does_not(tmp_path: Path) -> None:
    from trw_memory.lifecycle.verification_pass import VerifySettings

    from trw_mcp.state._daemon_store import DaemonMemoryStore

    client = _Client()
    store = DaemonMemoryStore(client, "project:test")  # type: ignore[arg-type]

    summary = store.verify("project:test", tmp_path, VerifySettings())

    assert client.calls == 2
    assert summary.entries_processed == 4 and summary.stale_transitions == 2
    assert not hasattr(summary, "a_count_from_a_later_minor")
