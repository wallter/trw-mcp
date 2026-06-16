"""Claude Code CC-01 channel: distill_snapshot.md writer.

Belongs to the ``channels/claude_code`` package (PRD-DIST-2405 FR11-FR17).

Writes ``distill_snapshot.md`` to the Anthropic auto-memory directory
(``~/.claude/projects/{project_id}/memory/``).  Uses ``ChannelLock`` from
the substrate for concurrent-session safety.

Key invariants:
- First synchronous write at ``init-project`` time (P1-01 fix) — snapshot
  is never absent for a session's first turn.
- TTL-skip logic prevents unnecessary rewrites (FR14).
- MEMORY.md index pointer managed via marker-replace (FR17).
- 8192-byte quota enforced (FR12).
- 190-line MEMORY.md warning threshold (FR11).
- Idempotent: same sidecar + tier → byte-identical output (NFR09) because
  timestamps are truncated to UTC day in the frontmatter.
"""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

import structlog

from trw_mcp.channels._lock import ChannelLock, ChannelLockSkip
from trw_mcp.channels._telemetry import append_channel_event
from trw_mcp.channels.claude_code._memory_path import (
    resolve_memory_dir,
)
from trw_mcp.channels.claude_code._snapshot_renderer import (
    SNAPSHOT_QUOTA_BYTES,
    render_snapshot,
)

log = structlog.get_logger(__name__)

__all__ = [
    "MEMORY_INDEX_MARKER_END",
    "MEMORY_INDEX_MARKER_START",
    "MEMORY_INDEX_NEAR_CAP_THRESHOLD",
    "WriteSnapshotResult",
    "update_memory_index",
    "write_distill_snapshot",
]

MEMORY_INDEX_MARKER_START: str = "<!-- trw-distill-index:start -->"
MEMORY_INDEX_MARKER_END: str = "<!-- trw-distill-index:end -->"
MEMORY_INDEX_NEAR_CAP_THRESHOLD: int = 190

_CHANNEL_ID: str = "cc-01-memory-distill-snapshot"
_LOCK_TIMEOUT_MS: int = 4000

WriteSnapshotStatus = Literal[
    "written",
    "skipped_ttl",
    "skipped_lock",
    "skipped_stale_t0_no_sidecar",
    "error",
]


class WriteSnapshotResult:
    """Result of a ``write_distill_snapshot`` call."""

    __slots__ = ("bytes_written", "snapshot_path", "status", "tier_used")

    def __init__(
        self,
        *,
        status: WriteSnapshotStatus,
        bytes_written: int | None = None,
        tier_used: str | None = None,
        snapshot_path: Path | None = None,
    ) -> None:
        self.status = status
        self.bytes_written = bytes_written
        self.tier_used = tier_used
        self.snapshot_path = snapshot_path

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "bytes_written": self.bytes_written,
            "tier_used": self.tier_used,
            "snapshot_path": str(self.snapshot_path) if self.snapshot_path else None,
        }


def _load_sidecar(sidecar_path: Path) -> dict[str, Any] | None:
    """Load and parse a sidecar JSON file; return None on any failure."""
    if not sidecar_path.exists():
        return None
    try:
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return None


def _is_t0_beacon(snapshot_path: Path) -> bool:
    """Return True if the existing snapshot is a T0 presence beacon."""
    if not snapshot_path.exists():
        return False
    try:
        content = snapshot_path.read_text(encoding="utf-8")
        # T0 beacons have "Tier: T0" in the frontmatter
        return "Tier: T0" in content
    except OSError:
        return False


def update_memory_index(
    memory_dir: Path,
    snapshot_filename: str = "distill_snapshot.md",
) -> None:
    """Update the MEMORY.md index pointer using marker-replace.

    Adds or replaces the distill index block inside MEMORY.md.
    Emits a ``memory_index_near_cap`` channel event if the file
    exceeds 190 lines after the update (FR17).

    Args:
        memory_dir: Path to the Claude Code memory directory.
        snapshot_filename: Filename of the snapshot inside *memory_dir*.
    """
    memory_index = memory_dir / "MEMORY.md"
    pointer_block = f"- [{snapshot_filename}]({snapshot_filename}) — TRW distill codebase intelligence snapshot"
    new_section = f"{MEMORY_INDEX_MARKER_START}\n{pointer_block}\n{MEMORY_INDEX_MARKER_END}"

    if memory_index.exists():
        original = memory_index.read_text(encoding="utf-8")
        if MEMORY_INDEX_MARKER_START in original:
            # Replace between markers
            import re

            updated = re.sub(
                re.escape(MEMORY_INDEX_MARKER_START) + r".*?" + re.escape(MEMORY_INDEX_MARKER_END),
                new_section,
                original,
                flags=re.DOTALL,
            )
        else:
            updated = original.rstrip("\n") + "\n\n" + new_section + "\n"
    else:
        updated = "# Memory Index\n\n" + new_section + "\n"

    memory_index.write_text(updated, encoding="utf-8")

    # Check line count warning threshold
    line_count = len(updated.splitlines())
    if line_count >= MEMORY_INDEX_NEAR_CAP_THRESHOLD:
        with suppress(Exception):
            append_channel_event(
                channel_id=_CHANNEL_ID,
                client="claude-code",
                event_type="memory_index_near_cap",
                tier=None,
                extra={"line_count": line_count},
            )
        log.warning(
            "memory_index_near_cap",
            line_count=line_count,
            threshold=MEMORY_INDEX_NEAR_CAP_THRESHOLD,
        )


def write_distill_snapshot(
    *,
    repo_root: Path,
    tier: str = "T2",
    force: bool = False,
    sidecar_path: Path | None = None,
    sha: str = "unknown",
    claude_projects_dir: Path | None = None,
) -> WriteSnapshotResult:
    """Write ``distill_snapshot.md`` to the Claude Code memory directory.

    Args:
        repo_root: Repository root used to derive the project ID.
        tier: Content tier ("T0", "T1", "T2", "T3").
        force: Bypass TTL and skip checks.
        sidecar_path: Override sidecar location (defaults to
            ``<repo_root>/.trw/distill/map-cache/before-edit-hint-<sha>.json``).
        sha: Git HEAD SHA; used for provenance + sidecar lookup.
        claude_projects_dir: Override for ``~/.claude/projects/``
            (used in tests).

    Returns:
        ``WriteSnapshotResult`` with status and bytes written.
    """
    memory_dir = resolve_memory_dir(repo_root, claude_projects_dir)
    snapshot_path = memory_dir / "distill_snapshot.md"

    # Resolve sidecar
    sidecar: dict[str, Any] | None = None
    if sidecar_path is not None:
        sidecar = _load_sidecar(sidecar_path)
    else:
        default_sidecar_path = repo_root / ".trw" / "distill" / "map-cache" / f"before-edit-hint-{sha}.json"
        sidecar = _load_sidecar(default_sidecar_path)

    # FR14: Skip if T0 beacon already written and sidecar still absent
    if not force and sidecar is None and tier != "T0" and _is_t0_beacon(snapshot_path):
        return WriteSnapshotResult(status="skipped_stale_t0_no_sidecar")

    # Try to acquire channel lock (non-blocking, 4s timeout)
    lock_path = repo_root / ".trw" / "channels" / f"{_CHANNEL_ID}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        lock = ChannelLock(lock_path, timeout_ms=_LOCK_TIMEOUT_MS)
        lock.__enter__()
    except ChannelLockSkip:
        log.debug("cc01_snapshot_lock_skip", snapshot_path=str(snapshot_path))
        return WriteSnapshotResult(status="skipped_lock")

    try:
        memory_dir.mkdir(parents=True, exist_ok=True)

        content = render_snapshot(
            channel_id=_CHANNEL_ID,
            sha=sha,
            tier=tier,
            sidecar=sidecar,
        )

        # Enforce quota: if over limit, tier down to T1, then T0
        if len(content.encode("utf-8")) > SNAPSHOT_QUOTA_BYTES:
            content = render_snapshot(
                channel_id=_CHANNEL_ID,
                sha=sha,
                tier="T1",
                sidecar=sidecar,
            )
        if len(content.encode("utf-8")) > SNAPSHOT_QUOTA_BYTES:
            content = render_snapshot(
                channel_id=_CHANNEL_ID,
                sha=sha,
                tier="T0",
                sidecar=None,
            )

        snapshot_path.write_text(content, encoding="utf-8")
        bytes_written = len(content.encode("utf-8"))

        # Update MEMORY.md index pointer
        update_memory_index(memory_dir)

        # Telemetry (fail-open)
        with suppress(Exception):
            append_channel_event(
                channel_id=_CHANNEL_ID,
                client="claude-code",
                event_type="push_write",
                tier=tier,
                bytes_emitted=bytes_written,
                extra={"sha": sha},
            )

        log.debug(
            "cc01_snapshot_written",
            snapshot_path=str(snapshot_path),
            bytes_written=bytes_written,
            tier=tier,
        )

        return WriteSnapshotResult(
            status="written",
            bytes_written=bytes_written,
            tier_used=tier,
            snapshot_path=snapshot_path,
        )

    except Exception as exc:
        log.warning("cc01_snapshot_error", error=str(exc))
        return WriteSnapshotResult(status="error")

    finally:
        with suppress(Exception):
            lock.__exit__(None, None, None)
