"""Shared helpers for PRD-CORE-208 delivery-operation tests.

Provides a UUIDv7 factory (the stdlib ``uuid`` module has no ``uuid7`` on 3.12)
and a coordinator/config factory over a real ``.trw`` directory so every test
exercises the durable SQLite store, not a mock.
"""

from __future__ import annotations

import os
import secrets
import time
import uuid
from pathlib import Path


def make_uuid7(ts_ms: int | None = None) -> str:
    """Build a canonical UUIDv7 whose embedded timestamp is ``ts_ms`` (or now)."""
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    ts = ts_ms & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (ts << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))


def strong_capability() -> str:
    """A caller recovery capability with well over 128 bits of entropy."""
    return secrets.token_hex(32)


def make_coordinator(trw_dir: Path, *, stale_lease_minutes: int = 15, queue_depth: int = 128):  # type: ignore[no-untyped-def]
    """Construct a DeliveryCoordinator bound to a real project-local ``.trw`` dir."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._delivery_operations import DeliveryCoordinator

    config = TRWConfig(
        delivery_stale_lease_minutes=stale_lease_minutes,
        delivery_queue_depth_max=queue_depth,
    )
    return DeliveryCoordinator(trw_dir, config=config, installation_identity="test-project")


def project_metadata_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Recursive (size, mtime_ns) snapshot for a zero-mutation assertion."""
    snapshot: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            snapshot[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def days_ms(days: float) -> int:
    return int(days * 24 * 60 * 60 * 1000)


def env_pid_dead() -> int:
    """Return a PID that is (almost certainly) not alive for takeover tests."""
    pid = 999_999
    try:
        os.kill(pid, 0)
    except OSError:
        return pid
    return 424_242
