"""The write + advisory-lock skeleton the FR03/FR06/FR07 state files share.

The override ledger (``ledger.py``), the open-violation marker (``violations.py``)
and the hook telemetry (``telemetry.py``) all persist small JSON documents under
the same rules, and the rules are the security-relevant part:

* ``O_NOFOLLOW`` on the final component, so a symlink swapped in for a state file
  raises ``ELOOP`` instead of silently retargeting the write;
* mode ``0o600`` on create, because every one of these files is enforcement state;
* ``fsync`` before close, so a block that was recorded stays recorded;
* ONE exclusive advisory lock held across the whole load -> mutate -> store
  window. A lock that spans only the store lets a concurrent writer persist a
  stale snapshot and silently drop another claim's open violation.

"Atomic" here means *with respect to other writers that take the same advisory
lock*. The write is an in-place ``O_TRUNC`` rewrite, NOT tmp-file + rename, so it
is deliberately not crash-atomic — the co-located ledger checkpoint and git
history are what make a truncation detectable (see ``ledger.py``'s threat model).

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from trw_mcp._locking import _lock_ex, _lock_un

__all__ = ["atomic_write_json", "locked"]


def atomic_write_json(path: Path, payload: object) -> None:
    """Rewrite *path* with *payload* as key-sorted, indented JSON plus a newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def locked(path: Path, *, append: bool = False) -> Iterator[int]:
    """Hold an exclusive advisory lock on *path* itself for the whole block.

    The open descriptor is yielded because ``ledger.py`` writes its append-only
    line through it (``append=True`` adds ``O_APPEND``); the load/mutate/store
    callers only need the lock and ignore the fd.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    if append:
        flags |= os.O_APPEND
    fd = os.open(str(path), flags, 0o600)
    try:
        _lock_ex(fd)
        yield fd
    finally:
        _lock_un(fd)
        os.close(fd)
