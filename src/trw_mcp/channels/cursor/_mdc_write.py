"""11-step MDC atomic write sequence (extracted from _mdc_emitter.py).

Belongs to the _mdc_emitter.py facade. Re-exported there for back-compat.

PRD-DIST-2401 Phase B+C.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.channels._conflict import RenderLog, detect_human_edit, write_atomic
from trw_mcp.channels._manifest_models import ChannelEntry, HumanEditDetection
from trw_mcp.channels._quota import check_quota, tier_down
from trw_mcp.channels._state import ChannelState, read_state, write_state
from trw_mcp.channels._ttl import check_staleness
from trw_mcp.channels.cursor._mdc_channel_entries import REGEN_CMD
from trw_mcp.channels.cursor._mdc_sidecar import get_sidecar_sha
from trw_mcp.channels.cursor._mdc_templates import (
    render_tombstone_mdc,
    validate_mdc_frontmatter,
)

log = structlog.get_logger(__name__)

__all__ = [
    "emit_mdc_under_lock",
    "tokens_from_bytes",
]

_BYTES_PER_TOKEN: float = 3.5


def tokens_from_bytes(n: int) -> int:
    """Estimate token count from byte count (conservative for code-heavy content)."""
    return max(1, int(n / _BYTES_PER_TOKEN))


def emit_mdc_under_lock(
    *,
    channel_id: str,
    entry: ChannelEntry,
    target_path: Path,
    repo_root: Path,
    sidecar: dict[str, Any],
    force: bool,
    dry_run: bool,
    render_t0: Callable[[], str],
    render_t1: Callable[[], str],
    render_log: RenderLog,
    emit_event: Callable[..., None],
) -> dict[str, Any]:
    """Execute the 11-step MDC emit sequence for a single channel file.

    Steps:
      2. Read ChannelState
      3. TTL check (tombstone if stale)
      4. Conflict detection (skip if human edit)
      5. Tier selection
      6. Quota enforcement with tier-down
      7. Frontmatter validation — abort on failure (FR13)
      8. dry_run return without writing
      9. Atomic write
     10. Write state
     11. Emit telemetry

    Args:
        channel_id: Channel identifier.
        entry: Manifest entry for this channel.
        target_path: Absolute path to the MDC file to write.
        repo_root: Repository root directory.
        sidecar: Distill sidecar dict.
        force: Skip TTL check and conflict detection.
        dry_run: Return content without writing.
        render_t0: Zero-arg callable returning T0 (beacon) content.
        render_t1: Zero-arg callable returning T1 (full) content.
        render_log: RenderLog instance for conflict detection.
        emit_event: Callable for telemetry events (fail-open).

    Returns:
        Result dict with status, channel_id, and per-outcome fields.
    """
    sidecar_sha = get_sidecar_sha(sidecar)
    channels_dir = repo_root / ".trw" / "channels"
    state_file = channels_dir / f"{channel_id}.state.yaml"

    # Step 2: Read state
    state = read_state(state_file)

    # Step 3: TTL check
    if not force:
        staleness = check_staleness(
            entry=entry,
            last_sidecar_sha=state.last_sidecar_sha if state else None,
            last_render_ts=state.last_render_ts if state else None,
            repo_root=repo_root,
        )
        if staleness.is_stale and not staleness.ttl_unknown:
            tombstone = render_tombstone_mdc(channel_id, REGEN_CMD, "ttl_exceeded")
            if not dry_run:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                write_atomic(target_path, tombstone, channel_id=channel_id, render_log=render_log)
            emit_event(channel_id, entry.client, "mdc_tombstone", outcome="written")
            return {"status": "tombstone", "channel_id": channel_id}

    # Step 4: Conflict detection
    if not force and target_path.exists():
        last_entry = render_log.last_for(channel_id, target_path)
        if last_entry is not None:
            human_edit = detect_human_edit(
                mode=HumanEditDetection.RENDER_LOG,
                target_path=target_path,
                expected_sha=last_entry.sha,
            )
            if human_edit:
                emit_event(
                    channel_id, entry.client, "mdc_conflict_skip",
                    outcome="preserved_human_edits", policy="skip",
                )
                return {"status": "skipped_conflict", "channel_id": channel_id, "conflict_detected": True}

    # Step 5: Tier selection
    tier = entry.tier_default or "T1"

    def render_at_tier(t: str) -> str:
        return render_t0() if t == "T0" else render_t1()

    content = render_at_tier(tier)

    # Step 6: Quota enforcement with tier-down
    quota = entry.quota_total_bytes
    while quota is not None and not check_quota(
        content_bytes=len(content.encode("utf-8")), quota_total_bytes=quota
    ):
        next_tier = tier_down(tier, tier_min=entry.tier_min)
        if next_tier == tier:
            break
        emit_event(channel_id, entry.client, "tier_down", from_tier=tier, to_tier=next_tier,
                   bytes_would_be=len(content.encode("utf-8")), outcome="tier_down")
        tier = next_tier
        content = render_at_tier(tier)

    # Step 7: Frontmatter validation
    valid, reason = validate_mdc_frontmatter(content)
    if not valid:
        log.warning(
            "mdc_frontmatter_validation_failed",
            channel_id=channel_id,
            reason=reason,
            outcome="write_aborted",
        )
        return {"status": "error", "channel_id": channel_id, "error": f"frontmatter validation failed: {reason}"}

    # Step 8: dry_run
    if dry_run:
        return {"status": "dry_run", "channel_id": channel_id, "tier_used": tier,
                "would_write": content, "bytes_would_be": len(content.encode("utf-8"))}

    # Step 9: Atomic write
    target_path.parent.mkdir(parents=True, exist_ok=True)
    log_entry = write_atomic(target_path, content, channel_id=channel_id,
                              render_log=render_log, sidecar_sha=sidecar_sha)

    # Step 10: Write state
    tokens = tokens_from_bytes(log_entry.bytes_written)
    new_state = ChannelState(
        channel_id=channel_id,
        last_sidecar_sha=sidecar_sha,
        last_render_ts=log_entry.ts,
        last_render_sha=log_entry.sha,
        last_render_tier=tier,
        last_render_bytes=log_entry.bytes_written,
        last_render_tokens_est=tokens,
    )
    write_state(new_state, state_file)

    # Step 11: Telemetry
    emit_event(channel_id, entry.client, "push_write", tier=tier,
               bytes_written=log_entry.bytes_written, tokens_estimated=tokens, outcome="written")

    return {"status": "written", "channel_id": channel_id, "tier_used": tier,
            "bytes_written": log_entry.bytes_written, "tokens_estimated": tokens}
