"""Channel-events JSONL appender + v1 schema constants.

Implements the canonical channel-event/v1 telemetry writer with:
- Fail-open on every I/O error (NFR06)
- 10 MB file rotation with single backup (FR09)
- 50,000-line cap with 25,000-line prune (FR09)
- Canonical record_id format validation (FR11 / SYS-02 fix)
- 20 canonical event_type values (FR10)

PRD-DIST-2400 FR08, FR09, FR10, FR11.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

__all__ = [
    "CHANNEL_EVENT_SCHEMA_VERSION",
    "CHANNEL_EVENT_V1_REQUIRED",
    "MAX_EVENTS_BYTES",
    "MAX_EVENTS_LINES",
    "PRUNE_LINES_ON_CAP",
    "RECORD_ID_PATH_KEYED_RE",
    "RECORD_ID_SLUG_KEYED_RE",
    "VALID_EVENT_TYPES",
    "append_channel_event",
    "prune_channel_events",
    "validate_record_id",
]

# ---------------------------------------------------------------------------
# Schema constants (FR10)
# ---------------------------------------------------------------------------

CHANNEL_EVENT_SCHEMA_VERSION = "channel-event/v1"

CHANNEL_EVENT_V1_REQUIRED: tuple[str, ...] = (
    "schema_version",
    "channel_id",
    "client",
    "ts",
    "event_type",
)

# Canonical 20 event_type values from master plan §6.2
VALID_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "push_write",
        "push_ephemeral",
        "pull_tool_call",
        "push_stale",
        "quota_exceeded",
        "tier_down",
        "channel_conflict",
        "snapshot_written",
        "snapshot_stale",
        "explorer_invoked",
        "explorer_completed",
        "edit_correlated",
        "hook_installed",
        "channel_disabled",
        "memory_index_near_cap",
        "mdc_tombstone",
        "mdc_conflict_skip",
        "subagent_outcome",
        "throttle_applied",
        "throttle_cleared",
    }
)

# ---------------------------------------------------------------------------
# Rotation / prune limits (FR09)
# ---------------------------------------------------------------------------

MAX_EVENTS_BYTES: int = 10 * 1024 * 1024  # 10 MB
MAX_EVENTS_LINES: int = 50_000
PRUNE_LINES_ON_CAP: int = 25_000

# ---------------------------------------------------------------------------
# record_id format patterns (FR11 / SYS-02 fix)
# ---------------------------------------------------------------------------

# path-keyed: "type:repo/relative/path@sha8+" (4-40 hex chars)
RECORD_ID_PATH_KEYED_RE: re.Pattern[str] = re.compile(r"^[a-z_]+:[^@]+@[a-f0-9]{4,40}$")

# slug-keyed: "type:slug" — alphanumeric, hyphens, underscores
RECORD_ID_SLUG_KEYED_RE: re.Pattern[str] = re.compile(r"^[a-z_]+:[a-z0-9_-]+$")

# ---------------------------------------------------------------------------
# Default log path
# ---------------------------------------------------------------------------

_DEFAULT_LOG_PATH = Path(".trw/telemetry/channel-events.jsonl")


def _resolve_log_path(log_path: Path | None) -> Path:
    """Return the effective log path, preferring TRW_REPO_ROOT env if set."""
    if log_path is not None:
        return log_path
    root_env = os.environ.get("TRW_REPO_ROOT")
    if root_env:
        return Path(root_env) / ".trw" / "telemetry" / "channel-events.jsonl"
    return _DEFAULT_LOG_PATH


# ---------------------------------------------------------------------------
# validate_record_id (FR11)
# ---------------------------------------------------------------------------


def validate_record_id(record_id: str) -> bool:
    """Return True if *record_id* matches a canonical format.

    Two valid forms:
    - Path-keyed: ``"type:repo/path@sha4-40hex"``
    - Slug-keyed: ``"type:slug"``

    Raises:
        ValueError: if neither pattern matches.
    """
    if RECORD_ID_PATH_KEYED_RE.match(record_id) or RECORD_ID_SLUG_KEYED_RE.match(record_id):
        return True
    raise ValueError(
        f"record_id {record_id!r} does not match canonical format ('<type>:<path>@<sha>' or '<type>:<slug>')"
    )


# ---------------------------------------------------------------------------
# _rotate — rename .jsonl → .jsonl.1
# ---------------------------------------------------------------------------


def _rotate(log_path: Path) -> None:
    """Rotate *log_path* to *log_path*.1.  Delete *.2 first if present."""
    backup1 = log_path.with_suffix(log_path.suffix + ".1")
    backup2 = log_path.with_suffix(log_path.suffix + ".2")
    if backup2.exists():
        backup2.unlink(missing_ok=True)
    if log_path.exists():
        os.rename(log_path, backup1)


# ---------------------------------------------------------------------------
# append_channel_event (FR08, FR09, FR10, FR11)
# ---------------------------------------------------------------------------


def append_channel_event(
    *,
    channel_id: str,
    client: str,
    event_type: str,
    log_path: Path | None = None,
    **optional_fields: Any,
) -> None:
    """Append one channel-event/v1 JSON line to the telemetry log.

    Unconditionally fail-open: never raises under any circumstances.

    Args:
        channel_id: Channel identifier.
        client: Client profile string (e.g. ``"claude-code"``).
        event_type: One of the 20 canonical event types.
        log_path: Override the default ``.trw/telemetry/channel-events.jsonl``.
        **optional_fields: Additional fields merged into the event record.
    """
    try:
        _write_channel_event(
            channel_id=channel_id,
            client=client,
            event_type=event_type,
            log_path=log_path,
            optional_fields=optional_fields,
        )
    except Exception as exc:
        log.debug(
            "channel_telemetry_write_failed",
            channel_id=channel_id,
            event_type=event_type,
            error=str(exc),
            outcome="telemetry_write_failed",
        )


def _write_channel_event(
    *,
    channel_id: str,
    client: str,
    event_type: str,
    log_path: Path | None,
    optional_fields: dict[str, Any],
) -> None:
    """Inner writer — may raise; caller wraps with fail-open try/except."""
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"event_type {event_type!r} is not in VALID_EVENT_TYPES")

    # Validate record_ids format if provided (warn but do NOT drop event)
    record_ids = optional_fields.get("record_ids")
    if record_ids is not None and isinstance(record_ids, list):
        for rid in record_ids:
            try:
                validate_record_id(str(rid))
            except ValueError:
                log.debug(
                    "channel_telemetry_invalid_record_id",
                    record_id=rid,
                    outcome="record_id_format_invalid",
                )

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    event: dict[str, Any] = {
        "schema_version": CHANNEL_EVENT_SCHEMA_VERSION,
        "channel_id": channel_id,
        "client": client,
        "ts": ts,
        "event_type": event_type,
    }
    # Merge optional fields (only non-None values)
    event.update({k: v for k, v in optional_fields.items() if v is not None})

    resolved = _resolve_log_path(log_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    # Rotation check: if file exceeds MAX_EVENTS_BYTES, rotate
    if resolved.exists() and resolved.stat().st_size > MAX_EVENTS_BYTES:
        _rotate(resolved)

    line = json.dumps(event, default=str) + "\n"
    with open(resolved, "a", encoding="utf-8") as fh:
        fh.write(line)


# ---------------------------------------------------------------------------
# prune_channel_events (FR09)
# ---------------------------------------------------------------------------


def prune_channel_events(
    log_path: Path,
    max_lines: int = MAX_EVENTS_LINES,
) -> int:
    """Prune *log_path* if it exceeds *max_lines* lines.

    Keeps the MOST RECENT ``max_lines - PRUNE_LINES_ON_CAP`` lines.
    Rewrites the file in-place.

    Returns:
        Number of lines pruned (0 if no pruning needed).
    Fail-open: returns 0 on any I/O error.
    """
    try:
        if not log_path.exists():
            return 0
        lines = log_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) <= max_lines:
            return 0
        keep = max_lines - PRUNE_LINES_ON_CAP
        pruned = len(lines) - keep
        kept_lines = lines[-keep:] if keep > 0 else []
        # Atomic rewrite
        fd, tmp_str = tempfile.mkstemp(
            dir=log_path.parent,
            prefix=f".{log_path.name}.prune.",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(kept_lines)
            os.rename(tmp_str, log_path)
        except Exception:
            try:
                os.unlink(tmp_str)
            except OSError:
                pass
            raise
        return pruned
    except Exception as exc:
        log.debug(
            "channel_telemetry_prune_failed",
            log_path=str(log_path),
            error=str(exc),
            outcome="prune_failed",
        )
        return 0
