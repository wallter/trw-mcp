"""SERIAL-RUN-LEAKS (A, test isolation only): the trw-mcp conftest resets the pinned-read cache around every test.

The cache (``trw_mcp._checkout_access``) is process-global and frees an entry only when its file is deleted or
replaced, so in one process the files earlier tests left in their (still existing) tmp_path dirs filled its 64 slots,
and a later test's journal read failed with read_capacity_exceeded (``test_delivery_status_tool`` in a serial run).
This is test isolation, NOT the product fix: a long-lived server still hits the cap
(tests/test_pinned_read_many_projects.py, the hold-aware eviction slice).

Both tests share an xdist group so they run in one process, in order: the first leaves the cache full on purpose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp import _checkout_access
from trw_mcp.tools._delivery_journal_store import JournalStore

pytestmark = pytest.mark.xdist_group("pinned-read-isolation")


def test_an_earlier_test_leaves_the_pinned_cache_full(tmp_path: Path) -> None:
    for n in range(_checkout_access._MAX_PINNED_FDS):
        path = tmp_path / f"f{n}"
        path.write_bytes(b"x" * 16)
        _checkout_access.read_at(path, 4)
    assert len(_checkout_access._fds) == _checkout_access._MAX_PINNED_FDS


def test_a_later_test_in_the_same_process_still_reads_a_new_journal(tmp_path: Path) -> None:
    store = JournalStore(tmp_path / ".trw" / "delivery" / "operations.sqlite3")
    store.connect().close()

    conn = store.connect_ro()
    try:
        assert store.read_schema_version(conn) >= 1
    finally:
        conn.close()
