"""Per-channel state persistence (ChannelState + read/write helpers).

Stores last-render metadata for conflict detection and TTL calculations.
Atomic write via temp-file + rename.

PRD-DIST-2400 FR06 prerequisite / Phase B.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict

log = structlog.get_logger(__name__)

__all__ = [
    "ChannelState",
    "read_state",
    "state_path_for",
    "write_state",
]


class ChannelState(BaseModel):
    """Per-channel render state persisted between sessions."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["channel-state/v1"] = "channel-state/v1"
    channel_id: str

    # SHA-256 of the last written file content
    last_render_sha: str | None = None
    # ISO-8601 UTC timestamp of last render
    last_render_ts: str | None = None
    # Tier used in last render
    last_render_tier: str | None = None
    # Estimated token count from last render
    last_render_tokens_est: int | None = None
    # Byte count from last render
    last_render_bytes: int | None = None
    # SHA-256 of segment interior (for SHA256_SEGMENT human-edit detection)
    segment_interior_sha256: str | None = None
    # SHA-256 of last sidecar file
    last_sidecar_sha: str | None = None
    # git commit count at last TTL check
    ttl_commit_count_at_last_check: int | None = None


def state_path_for(channel_id: str, channels_dir: Path) -> Path:
    """Return the canonical state file path for a channel.

    Args:
        channel_id: The channel identifier string.
        channels_dir: Base directory for channel state files.

    Returns:
        Path pointing to ``<channels_dir>/<channel_id>-state.json``.
    """
    return channels_dir / f"{channel_id}-state.json"


def read_state(state_path: Path) -> ChannelState | None:
    """Read and parse a ChannelState from *state_path*.

    Returns:
        Parsed ``ChannelState`` on success, or ``None`` if the file does not
        exist or cannot be parsed. Never raises.
    """
    try:
        text = state_path.read_text(encoding="utf-8")
        data = json.loads(text)
        return ChannelState.model_validate(data)
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.debug(
            "channel_state_read_failed",
            state_path=str(state_path),
            error=str(exc),
            outcome="state_unreadable",
        )
        return None


def write_state(state: ChannelState, state_path: Path) -> None:
    """Atomically write *state* to *state_path*.

    Uses a temp file + ``os.rename`` for crash-safe atomicity.
    Creates parent directories if necessary.

    Args:
        state: The ``ChannelState`` instance to persist.
        state_path: Destination path for the JSON state file.
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = state.model_dump_json(indent=2)

    fd, tmp_path_str = tempfile.mkstemp(
        dir=state_path.parent,
        prefix=f".{state_path.name}.tmp.",
        suffix=".json",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.rename(tmp_path_str, state_path)
        log.debug(
            "channel_state_written",
            state_path=str(state_path),
            channel_id=state.channel_id,
            outcome="ok",
        )
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise
