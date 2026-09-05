"""Shared helpers for the PRD-CORE-208 / PRD-FIX-127 delivery-operation tests.

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


# --- PRD-FIX-127 resume-test fixtures (shared by test_delivery_resume*.py) ---

_TWO_HOURS_MS = 2 * 60 * 60 * 1000


def age_lease(coord, did: str) -> int:  # type: ignore[no-untyped-def]
    """Push the lease expiry two hours into the past; return the current revision."""
    conn = coord.store.connect()
    with coord.store.immediate(conn):
        op = coord.store.get_operation(conn, did)
        assert op is not None
        coord.store.replace_operation(
            conn, op.model_copy(update={"lease_expiry_utc_ms": coord._now_ms() - _TWO_HOURS_MS})
        )
    conn.close()
    return op.revision


def recovery_events(coord, did: str):  # type: ignore[no-untyped-def]
    conn = coord.store.connect()
    try:
        return coord.store.get_recovery_events(conn, did)
    finally:
        conn.close()


def steps_by_id(coord, did: str):  # type: ignore[no-untyped-def]
    conn = coord.store.connect()
    try:
        return {s.effect_id: s for s in coord.store.get_steps(conn, did)}
    finally:
        conn.close()


def operation_row(coord, did: str):  # type: ignore[no-untyped-def]
    conn = coord.store.connect()
    try:
        return coord.store.get_operation(conn, did)
    finally:
        conn.close()


def seed_deliver_run(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    for sub in ("learnings/entries", "reflections", "context"):
        (trw_dir / sub).mkdir(parents=True, exist_ok=True)
    run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(
        "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n", encoding="utf-8"
    )
    (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    return run_dir


def deliver_patches(tmp_path: Path):  # type: ignore[no-untyped-def]
    """The standard patch set that drives a real trw_deliver on a synthetic run."""
    from unittest.mock import patch

    trw_dir = tmp_path / ".trw"
    run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
    return (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
        patch(
            "trw_mcp.tools.ceremony._do_instruction_sync",
            return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
        ),
        patch(
            "trw_mcp.tools._deferred_delivery._do_index_sync",
            return_value={"status": "success", "index": {}, "roadmap": {}},
        ),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
    )
