"""SERIAL-RUN-LEAKS: one long-lived process reads the delivery journals of many projects without running out of room.

The pinned-read cache (``_checkout_access``) keeps one descriptor per journal/mailbox inode for the process's life and
refuses a new one once 64 are live. A file is released only when it is deleted or replaced, never when it is idle.
A shared MCP server (``shared_server``: one process per env, every client's checkout behind it) therefore stopped
reading any delivery journal after about 64 live projects; a serial ~25k-test run hit the same wall
(``test_delivery_status_tool``: read_capacity_exceeded). This drives the product path, not a test artefact.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._checkout_access_state import reset_pinned_reads
from trw_mcp.tools._delivery_journal_store import JournalStore

_PROJECTS = 200  # well past the cap; this machine has ~250 worktrees a shared server could serve


@pytest.fixture(autouse=True)
def _isolated_cache() -> None:
    reset_pinned_reads()
    yield
    reset_pinned_reads()


def test_a_process_reads_the_journals_of_more_projects_than_the_cache_holds(tmp_path: Path) -> None:
    stores = []
    for n in range(_PROJECTS):
        store = JournalStore(tmp_path / f"project-{n}" / ".trw" / "delivery" / "operations.sqlite3")
        store.connect().close()
        stores.append(store)

    for store in stores:  # every project stays live (no file deleted or replaced), as in a long-lived server
        conn = store.connect_ro()
        try:
            assert store.read_schema_version(conn) >= 1
        finally:
            conn.close()
