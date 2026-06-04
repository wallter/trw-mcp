"""Generic 11-step instruction-segment renderer.

Reused by Codex (PRD-DIST-2402), opencode (PRD-DIST-2403), Antigravity
(PRD-DIST-2404), and Copilot (PRD-DIST-2406) via client-specific
``content_for_tier`` callbacks.

PRD-DIST-2400 §3 Shared Primitives / instruction_segment/_renderer.py.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict

from trw_mcp.channels._conflict import RenderLog, detect_human_edit, reconcile, write_atomic
from trw_mcp.channels._lock import ChannelLock, ChannelLockSkip
from trw_mcp.channels._manifest_models import ChannelEntry, HumanEditDetection
from trw_mcp.channels._provenance import now_utc_iso8601, render_provenance_comment
from trw_mcp.channels._quota import enforce_quota_with_tier_down
from trw_mcp.channels._state import ChannelState, read_state, state_path_for, write_state
from trw_mcp.channels._telemetry import append_channel_event
from trw_mcp.channels._ttl import check_staleness

log = structlog.get_logger(__name__)

__all__ = [
    "InstructionSegmentResult",
    "render_instruction_segment",
]

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

_Status = Literal[
    "written",
    "skipped_lock",
    "skipped_conflict",
    "skipped_ttl",
    "skipped_quota_exempt",
    "dry_run",
    "error",
]


class InstructionSegmentResult(BaseModel):
    """Outcome of a single instruction-segment render attempt."""

    model_config = ConfigDict(extra="forbid")

    channel_id: str
    status: _Status
    tier_used: str | None = None
    bytes_written: int | None = None
    tokens_estimated: int | None = None
    would_write: str | None = None
    conflict_detected: bool = False
    ttl_commits_remaining: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# 11-step renderer
# ---------------------------------------------------------------------------

_DEFAULT_CHANNELS_DIR = Path(".trw/channels")
_DEFAULT_TELEMETRY_LOG: Path | None = None  # falls through to _telemetry default


def render_instruction_segment(
    *,
    entry: ChannelEntry,
    repo_root: Path,
    sidecar_sha: str | None,
    content_for_tier: Callable[[str], str],
    target_file: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> InstructionSegmentResult:
    """Execute the 11-step canonical render sequence for an instruction segment.

    Steps:
      1. Resolve target_file (default from entry.file)
      2. Acquire ChannelLock; on ChannelLockSkip → status="skipped_lock"
      3. Read ChannelState (or None if absent)
      4. Check TTL via check_staleness(); stale + not force → skipped_ttl
      5. Detect human edit unless force=True; conflict → status="skipped_conflict"
      6. Choose tier (entry.tier_default)
      7. Render content at chosen tier via content_for_tier(tier)
      8. Enforce quota: enforce_quota_with_tier_down(); may tier-down
      9. Prepend provenance comment; wrap in markers
     10. dry_run → return would_write without writing
         else write_atomic; write_state
     11. Emit channel event (fail-open telemetry)

    Args:
        entry: Manifest entry describing the channel.
        repo_root: Repository root directory.
        sidecar_sha: Git SHA of the distill sidecar, or None if unavailable.
        content_for_tier: Callback receiving a tier string and returning
            rendered content appropriate for that tier.
        target_file: Override for the write destination. Defaults to
            ``repo_root / entry.file`` when *entry.file* is set.
        force: Skip TTL check and conflict detection.
        dry_run: Return the would-be content without writing to disk.

    Returns:
        ``InstructionSegmentResult`` describing the outcome.
    """
    channel_id: str = entry.id

    # ------------------------------------------------------------------
    # Step 1: Resolve target_file
    # ------------------------------------------------------------------
    if target_file is None:
        if entry.file:
            target_file = repo_root / entry.file
        else:
            return InstructionSegmentResult(
                channel_id=channel_id,
                status="error",
                error="entry.file is not set and target_file was not provided",
            )

    # ------------------------------------------------------------------
    # Step 2: Acquire lock
    # ------------------------------------------------------------------
    lock_path: Path
    if entry.lock_file:
        lock_path = repo_root / entry.lock_file
    else:
        lock_path = repo_root / ".trw" / "channels" / f"{channel_id}.lock"

    try:
        lock = ChannelLock(lock_path)
        lock.__enter__()
    except ChannelLockSkip:
        log.debug(
            "instruction_segment_lock_skip",
            channel_id=channel_id,
            lock_path=str(lock_path),
            outcome="skipped_lock",
        )
        # HIGH-1 fix: emit channel_lock_skip (not channel_conflict) — distinct
        # event so lock contention does not corrupt the write-conflict signal.
        _emit(
            channel_id=channel_id,
            client=entry.client,
            event_type="channel_lock_skip",
            tier=None,
            outcome="skipped_lock",
        )
        return InstructionSegmentResult(
            channel_id=channel_id,
            status="skipped_lock",
        )

    try:
        return _render_under_lock(
            entry=entry,
            repo_root=repo_root,
            sidecar_sha=sidecar_sha,
            content_for_tier=content_for_tier,
            target_file=target_file,
            channel_id=channel_id,
            force=force,
            dry_run=dry_run,
        )
    except Exception as exc:
        log.warning(
            "instruction_segment_error",
            channel_id=channel_id,
            error=str(exc),
            outcome="error",
        )
        # HIGH-1 fix: emit channel_error (not channel_conflict) — distinct
        # event so internal errors do not corrupt the write-conflict signal.
        _emit(
            channel_id=channel_id,
            client=entry.client,
            event_type="channel_error",
            tier=None,
            outcome="error",
        )
        return InstructionSegmentResult(
            channel_id=channel_id,
            status="error",
            error=str(exc),
        )
    finally:
        try:
            lock.__exit__(None, None, None)
        except Exception:
            pass


def _render_under_lock(
    *,
    entry: ChannelEntry,
    repo_root: Path,
    sidecar_sha: str | None,
    content_for_tier: Callable[[str], str],
    target_file: Path,
    channel_id: str,
    force: bool,
    dry_run: bool,
) -> InstructionSegmentResult:
    """Execute steps 3-11 while the channel lock is held."""

    channels_dir = repo_root / ".trw" / "channels"
    state_file = state_path_for(channel_id, channels_dir)

    # ------------------------------------------------------------------
    # Step 3: Read ChannelState
    # ------------------------------------------------------------------
    state = read_state(state_file)

    # Initialise render_log here (shared between reconcile at Step 5 and
    # write_atomic at Step 10) so crash-recovery is always available.
    render_log = RenderLog(channels_dir / "render-log.jsonl")

    # ------------------------------------------------------------------
    # Step 4: TTL check
    # ------------------------------------------------------------------
    ttl_commits_remaining: int | None = None
    if not force:
        ttl_result = check_staleness(
            entry=entry,
            last_sidecar_sha=sidecar_sha,
            last_render_ts=state.last_render_ts if state else None,
            repo_root=repo_root,
        )
        if ttl_result.ttl_unknown:
            pass  # SYS-03: proceed as not-stale when git HEAD is detached
        elif ttl_result.is_stale:
            log.debug(
                "instruction_segment_ttl_stale",
                channel_id=channel_id,
                ttl_commits_remaining=ttl_result.ttl_commits_remaining,
                outcome="skipped_ttl",
            )
            _emit(
                channel_id=channel_id,
                client=entry.client,
                event_type="push_stale",
                tier=None,
                outcome="skipped_ttl",
            )
            return InstructionSegmentResult(
                channel_id=channel_id,
                status="skipped_ttl",
                ttl_commits_remaining=0,
            )
        else:
            # MED-6: ttl_commits_remaining is now computed in check_staleness
            ttl_commits_remaining = ttl_result.ttl_commits_remaining

    # ------------------------------------------------------------------
    # Step 5: Human-edit / conflict detection (HIGH-3 fix: reconcile first)
    # ------------------------------------------------------------------
    # Call reconcile() before detect_human_edit so a crash between log-append
    # and os.rename does not permanently skip this channel (FR07 crash-recovery).
    reconcile(
        channel_id=channel_id,
        target_path=target_file,
        render_log=render_log,
    )

    conflict_detected = False
    if not force:
        expected_sha = state.segment_interior_sha256 if state else None
        mode_val: str = (
            entry.human_edit_detection.value
            if hasattr(entry.human_edit_detection, "value")
            else str(entry.human_edit_detection)
        )
        # Map str → HumanEditDetection enum for the helper
        try:
            mode_enum = HumanEditDetection(mode_val)
        except ValueError:
            mode_enum = HumanEditDetection.NONE

        conflict_detected = detect_human_edit(
            mode=mode_enum,
            target_path=target_file,
            expected_sha=expected_sha,
            markers=entry.markers,
        )

        if conflict_detected:
            log.debug(
                "instruction_segment_conflict",
                channel_id=channel_id,
                target_file=str(target_file),
                outcome="skipped_conflict",
            )
            _emit(
                channel_id=channel_id,
                client=entry.client,
                event_type="channel_conflict",
                tier=None,
                outcome="skipped_conflict",
            )
            return InstructionSegmentResult(
                channel_id=channel_id,
                status="skipped_conflict",
                conflict_detected=True,
            )

    # ------------------------------------------------------------------
    # Step 6: Choose tier
    # ------------------------------------------------------------------
    tier: str = entry.tier_default

    # ------------------------------------------------------------------
    # Steps 7 + 8: Render + quota enforcement
    # ------------------------------------------------------------------
    final_content, tier_used = enforce_quota_with_tier_down(
        content="",  # seed — render_at_tier generates the real content
        current_tier=tier,
        quota_total_bytes=entry.quota_total_bytes,
        tier_min=entry.tier_min,
        render_at_tier=content_for_tier,
    )

    # ------------------------------------------------------------------
    # Step 9: Prepend provenance + wrap in markers
    # ------------------------------------------------------------------
    provenance = render_provenance_comment(
        channel_id=channel_id,
        sha=sidecar_sha or "unknown",
        ts=now_utc_iso8601(),
        tier=tier_used,
        regenerate=entry.regenerate_cmd or "",
    )
    wrapped = _wrap_with_markers(
        provenance=provenance,
        content=final_content,
        start=entry.markers.start,
        end=entry.markers.end,
    )

    bytes_written = len(wrapped.encode("utf-8"))
    tokens_estimated = len(wrapped.split())  # rough word-count approximation

    # ------------------------------------------------------------------
    # Step 10: Write or dry-run
    # ------------------------------------------------------------------
    if dry_run:
        _emit(
            channel_id=channel_id,
            client=entry.client,
            event_type="push_ephemeral",
            tier=tier_used,
            outcome="dry_run",
            bytes_written=bytes_written,
        )
        return InstructionSegmentResult(
            channel_id=channel_id,
            status="dry_run",
            tier_used=tier_used,
            bytes_written=bytes_written,
            tokens_estimated=tokens_estimated,
            would_write=wrapped,
            ttl_commits_remaining=ttl_commits_remaining,
        )

    write_atomic(
        target_file,
        wrapped,
        channel_id=channel_id,
        render_log=render_log,
        sidecar_sha=sidecar_sha,
    )

    # Persist updated state
    seg_sha = hashlib.sha256(final_content.encode("utf-8")).hexdigest()
    new_state = ChannelState(
        channel_id=channel_id,
        last_render_tier=tier_used,
        last_render_bytes=bytes_written,
        last_render_tokens_est=tokens_estimated,
        last_sidecar_sha=sidecar_sha,
        segment_interior_sha256=seg_sha,
        last_render_ts=now_utc_iso8601(),
    )
    write_state(new_state, state_file)

    # ------------------------------------------------------------------
    # Step 11: Emit telemetry (fail-open)
    # ------------------------------------------------------------------
    _emit(
        channel_id=channel_id,
        client=entry.client,
        event_type="push_write",
        tier=tier_used,
        outcome="written",
        bytes_written=bytes_written,
    )

    return InstructionSegmentResult(
        channel_id=channel_id,
        status="written",
        tier_used=tier_used,
        bytes_written=bytes_written,
        tokens_estimated=tokens_estimated,
        ttl_commits_remaining=ttl_commits_remaining,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wrap_with_markers(
    *,
    provenance: str,
    content: str,
    start: str,
    end: str,
) -> str:
    """Return *content* wrapped in markers with provenance prepended.

    When *start*/*end* are empty strings the content is returned as-is
    (no marker wrapper, provenance still prepended).
    """
    if not start and not end:
        return provenance + "\n" + content if provenance else content

    parts = [provenance, start, content, end]
    return "\n".join(p for p in parts if p) + "\n"


def _emit(
    *,
    channel_id: str,
    client: str,
    event_type: str,
    tier: str | None,
    outcome: str,
    bytes_written: int | None = None,
) -> None:
    """Fail-open telemetry wrapper."""
    try:
        append_channel_event(
            channel_id=channel_id,
            client=client,
            event_type=event_type,
            tier=tier,
            bytes_emitted=bytes_written,
            extra={"outcome": outcome},
        )
    except Exception:
        pass
