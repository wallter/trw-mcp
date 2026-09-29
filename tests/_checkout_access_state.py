"""Reset ``trw_mcp._checkout_access``'s process-global pinned-read cache between tests.

The cache pins one fd per inode and is capped at ``_MAX_PINNED_FDS`` (64). A pytest worker that has read
files through ``read_at`` in earlier tests may already sit at the cap, and then a test that reads a NEW file
gets ``PinnedReadCapacityExceeded`` (an OSError) that the journal classifier reads as "not legacy WAL", so a
legacy-WAL file is reported ``corrupt_store``. Tests that depend on the cache having room reset it.
"""

from __future__ import annotations

import os

from trw_mcp import _checkout_access


def reset_pinned_reads() -> None:
    """Close every pinned descriptor and clear the cache's bookkeeping."""
    for fd in list(_checkout_access._fds.values()) + _checkout_access._race_loser_fds:
        try:
            os.close(fd)
        except OSError:  # trw-fail-silent-allow: best-effort teardown close; a double-close is harmless to isolate
            pass
    _checkout_access._fds.clear()
    _checkout_access._path_inode.clear()
    _checkout_access._inode_paths.clear()
    _checkout_access._inode_locks.clear()
    _checkout_access._race_loser_fds.clear()
