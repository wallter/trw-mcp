"""Cleanup actions for stale/expired channels.

Implements the six CleanupAction implementations per PRD-DIST-2400 FR20.

T0 beacon exemption rule:
    FULL_PRUNE and CLEAR_SEGMENT skip when entry.tier_default == "T0" or
    the target file's content is already a T0 beacon.  Only SUPPRESS can
    produce a no-content outcome for a T0 channel.

PRD-DIST-2400 Phase C.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.channels._manifest_models import (
    ChannelEntry,
    CleanupAction,
    CleanupTrigger,
    MarkersConfig,
)
from trw_mcp.channels._marker_replace import replace_distill_segment
from trw_mcp.channels._quota import tier_down
from trw_mcp.channels._telemetry import append_channel_event

log = structlog.get_logger(__name__)

__all__ = [
    "cleanup_channel",
    "is_t0_beacon",
    "tombstone_content",
]

# T0 beacon is defined as very short content that contains a provenance
# marker and lacks substantive body sections.
_T0_BEACON_MAX_CHARS = 200
_T0_BEACON_KEYWORD = "PROVENANCE"


def is_t0_beacon(content: str) -> bool:
    """Heuristic: return True when *content* looks like a T0 presence beacon.

    A T0 beacon is the minimum renderable content for any channel — a
    short provenance comment with no substantive body.  Heuristic criteria:
    - Shorter than 200 characters, AND
    - Contains the string "PROVENANCE".

    Args:
        content: File content to check.

    Returns:
        True if the content matches T0 beacon heuristics.
    """
    stripped = content.strip()
    return len(stripped) < _T0_BEACON_MAX_CHARS and _T0_BEACON_KEYWORD in stripped


def tombstone_content(
    *,
    channel_id: str,
    regenerate_cmd: str,
    reason: str,
) -> str:
    """Return the text for a tombstone stale-notice file.

    Args:
        channel_id: Identifier of the stale channel.
        regenerate_cmd: Command operator should run to regenerate.
        reason: Human-readable reason for the tombstone.

    Returns:
        Tombstone file content string.
    """
    return (
        f"<!-- TRW DISTILL STALE\n"
        f"channel_id: {channel_id}\n"
        f"reason: {reason}\n"
        f"regenerate: {regenerate_cmd}\n"
        f"-->\n"
        f"\n"
        f"[TRW DISTILL STALE — run: {regenerate_cmd}]\n"
    )


def _is_t0_exempt(entry: ChannelEntry, target_path: Path) -> bool:
    """Return True when the T0 beacon exemption applies.

    Checks both the manifest tier_default and the current file content.
    """
    # tier_default stored as str via use_enum_values=True
    tier_val: str = str(entry.tier_default)
    if tier_val == "T0":
        return True
    if target_path.exists():
        try:
            content = target_path.read_text(encoding="utf-8")
            return is_t0_beacon(content)
        except OSError:
            pass
    return False


def cleanup_channel(
    *,
    entry: ChannelEntry,
    target_path: Path,
    trigger: CleanupTrigger,
) -> dict[str, str]:
    """Execute the cleanup action defined in *entry.cleanup.action*.

    Args:
        entry: The manifest ChannelEntry (provides cleanup config, markers,
            tier settings).
        target_path: File path targeted by this channel.
        trigger: The trigger that initiated this cleanup (for telemetry).

    Returns:
        Status dict with at minimum ``{"status": <str>}``.  Extra fields
        may be present (e.g. ``tier_used``, ``path``).
    """
    # Normalise action to canonical value string.
    # CleanupConfig lacks use_enum_values so the field may be an enum instance
    # or a str depending on construction path.
    raw_action = entry.cleanup.action
    action_str: str = raw_action.value if isinstance(raw_action, CleanupAction) else str(raw_action)

    # ---- SUPPRESS ----
    if action_str == CleanupAction.SUPPRESS.value:
        log.debug(
            "cleanup_suppress",
            channel_id=entry.id,
            outcome="suppressed",
        )
        return {"status": "suppressed"}

    # ---- NONE ----
    if action_str == CleanupAction.NONE.value:
        return {"status": "noop"}

    # ---- T0 beacon exemption for destructive actions ----
    if action_str in (CleanupAction.FULL_PRUNE.value, CleanupAction.CLEAR_SEGMENT.value) and _is_t0_exempt(
        entry, target_path
    ):
        log.debug(
            "cleanup_t0_exempt",
            channel_id=entry.id,
            action=action_str,
            outcome="skipped_t0_exempt",
        )
        return {"status": "skipped_t0_exempt"}

    # ---- FULL_PRUNE ----
    if action_str == CleanupAction.FULL_PRUNE.value:
        if target_path.exists():
            try:
                target_path.unlink()
            except OSError as exc:
                log.debug(
                    "cleanup_full_prune_failed",
                    channel_id=entry.id,
                    path=str(target_path),
                    error=str(exc),
                    outcome="prune_failed",
                )
                return {"status": "prune_failed", "error": str(exc)}
        append_channel_event(
            channel_id=entry.id,
            client=entry.client,
            event_type="channel_disabled",
            outcome="pruned",
        )
        log.debug(
            "cleanup_full_prune",
            channel_id=entry.id,
            path=str(target_path),
            outcome="pruned",
        )
        return {"status": "pruned", "path": str(target_path)}

    # ---- CLEAR_SEGMENT ----
    if action_str == CleanupAction.CLEAR_SEGMENT.value:
        try:
            existing = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
            markers: MarkersConfig = entry.markers
            cleared = replace_distill_segment(existing, "", markers=markers)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(cleared, encoding="utf-8")
        except OSError as exc:
            log.debug(
                "cleanup_clear_segment_failed",
                channel_id=entry.id,
                path=str(target_path),
                error=str(exc),
                outcome="clear_failed",
            )
            return {"status": "clear_failed", "error": str(exc)}
        log.debug(
            "cleanup_clear_segment",
            channel_id=entry.id,
            path=str(target_path),
            outcome="segment_cleared",
        )
        return {"status": "segment_cleared", "path": str(target_path)}

    # ---- TOMBSTONE ----
    if action_str == CleanupAction.TOMBSTONE.value:
        regenerate_cmd = f"trw-mcp channel-render {entry.id} --force"
        content = tombstone_content(
            channel_id=entry.id,
            regenerate_cmd=regenerate_cmd,
            reason=str(trigger),
        )
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            log.debug(
                "cleanup_tombstone_failed",
                channel_id=entry.id,
                path=str(target_path),
                error=str(exc),
                outcome="tombstone_failed",
            )
            return {"status": "tombstone_failed", "error": str(exc)}
        log.debug(
            "cleanup_tombstone",
            channel_id=entry.id,
            path=str(target_path),
            outcome="tombstone_written",
        )
        return {"status": "tombstone_written", "path": str(target_path)}

    # ---- TIER_DOWN ----
    if action_str == CleanupAction.TIER_DOWN.value:
        lower = tier_down(entry.tier_default)
        log.debug(
            "cleanup_tier_down",
            channel_id=entry.id,
            from_tier=entry.tier_default,
            to_tier=lower,
            outcome="tier_down",
        )
        return {"status": "tier_down", "tier_used": lower}

    # ---- TIER_DOWN_TO_T0 ----
    if action_str == CleanupAction.TIER_DOWN_TO_T0.value:
        log.debug(
            "cleanup_tier_down_to_t0",
            channel_id=entry.id,
            outcome="tier_down_to_t0",
        )
        return {"status": "tier_down_to_t0", "tier_used": "T0"}

    # Unknown action — treat as noop
    log.debug(
        "cleanup_unknown_action",
        channel_id=entry.id,
        action=action_str,
        outcome="noop",
    )
    return {"status": "noop", "action": action_str}
