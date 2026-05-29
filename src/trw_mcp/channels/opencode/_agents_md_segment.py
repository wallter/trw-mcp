"""opencode AGENTS.md distill segment renderer.

# Managed by TRW — no trw_distill imports permitted.

Implements the ``opencode-agents-md-segment`` channel using the shared
11-step renderer from PRD-DIST-2400 instruction_segment/_renderer.py.

Markers:  ``<!-- trw:distill:start -->`` / ``<!-- trw:distill:end -->``
Tier:     T1 default (800 token / 6144-byte budget)

Sequential placement: distill section appears AFTER ``<!-- trw:end -->``
(the ceremony section end marker), never nested inside it.

Audit fixes applied:
- P0-06: acquires shared ``.trw/channels/agents-md.lock`` before write
- P2-10: IP filter strips ``trw-distill/`` paths before render

PRD-DIST-2403 FR01-FR09.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.channels._conflict import RenderLog, write_atomic
from trw_mcp.channels._manifest_models import (
    ChannelEntry,
    ChannelStatus,
    ChannelSurface,
    CleanupAction,
    CleanupConfig,
    CleanupTrigger,
    HumanEditDetection,
    MarkersConfig,
    WriteStrategy,
)
from trw_mcp.channels._marker_replace import replace_distill_segment
from trw_mcp.channels._provenance import now_utc_iso8601, render_provenance_comment
from trw_mcp.channels._quota import enforce_quota_with_tier_down
from trw_mcp.channels._state import ChannelState, state_path_for, write_state
from trw_mcp.channels._telemetry import append_channel_event
from trw_mcp.channels.instruction_segment import (
    InstructionSegmentResult,
)
from trw_mcp.channels.opencode._ip_filter import filter_proprietary_paths
from trw_mcp.channels.opencode._shared_lock import ChannelLockSkip, agents_md_lock

log = structlog.get_logger(__name__)

# Untyped sidecar JSON payload — type alias to avoid dict[str, Any] in public
# function signatures (NFR04).  The sidecar format is a file contract
# (risk-report-sidecar/v0) whose schema is outside this package.
SidecarData = dict[str, Any]

__all__ = [
    "DISTILL_BEGIN",
    "DISTILL_END",
    "T1_BYTE_QUOTA",
    "T1_TOKEN_BUDGET",
    "SidecarData",
    "build_opencode_agents_md_entry",
    "install_opencode_agents_md_distill_segment",
]

# ---------------------------------------------------------------------------
# Marker constants (sequential, NOT nested in <!-- trw:end -->)
# ---------------------------------------------------------------------------

DISTILL_BEGIN = "<!-- trw:distill:start -->"
DISTILL_END = "<!-- trw:distill:end -->"

# T1 budget (FR02): 800 tokens (~6144 bytes)
T1_TOKEN_BUDGET = 800
T1_BYTE_QUOTA = 6144  # hard byte cap (FR09 / NFR05)

# Stale notice appended when sidecar SHA doesn't match HEAD (FR04)
_STALE_NOTICE = (
    "\n\n> **(STALE — sidecar outdated, run: trw-distill self-improve risk-report --repo . --persist-sidecar)**"
)

# Footer line (FR02)
_T1_FOOTER = "\nUse /trw-before-edit <path> for file-specific context."


# ---------------------------------------------------------------------------
# Content builders
# ---------------------------------------------------------------------------


def _format_hotspot(entry: dict[str, Any], idx: int) -> str:
    path = entry.get("file", entry.get("path", "unknown"))
    score = entry.get("composite_score", entry.get("risk_score", entry.get("score", 0.0)))
    score_str = f"{float(score):.2f}"
    return f"{idx}. `{path}` (risk: {score_str})"


def _format_convention(conv: str | dict[str, Any]) -> str:
    if isinstance(conv, dict):
        text = conv.get("text", conv.get("description", str(conv)))
    else:
        text = str(conv)
    return f"- {text}"


def _t0_beacon(distill_action: str) -> str:
    """T0 tier: two-line beacon when sidecar absent (FR03)."""
    return (
        "## TRW Distill — Intelligence Unavailable\n\n"
        f"Run `{distill_action}` to generate distill intelligence for this project."
    )


def _t1_content(sidecar_data: dict[str, Any], *, stale: bool = False) -> str:
    """T1 tier: top-5 hotspots + top-3 conventions (FR02 / FR04)."""
    raw_hotspots: list[dict[str, Any]] = sidecar_data.get("hotspots", [])
    conventions: list[Any] = sidecar_data.get("conventions", [])

    # Apply IP filter (P2-10 / FR07)
    paths = [h.get("file", h.get("path", "")) for h in raw_hotspots]
    filtered_paths = set(filter_proprietary_paths(paths))
    hotspots = [h for h in raw_hotspots if h.get("file", h.get("path", "")) in filtered_paths]

    top_spots = hotspots[:5]
    top_convs = conventions[:3]

    lines: list[str] = ["## TRW Distill — Codebase Intelligence (T1)\n"]

    lines.append("### High-Risk Files\n")
    for i, h in enumerate(top_spots, 1):
        lines.append(_format_hotspot(h, i))
    if not top_spots:
        lines.append("_No hotspot data available._")

    lines.append("\n### Project Conventions\n")
    lines.extend(_format_convention(c) for c in top_convs)
    if not top_convs:
        lines.append("_No convention data available._")

    lines.append(_T1_FOOTER)

    if stale:
        lines.append(_STALE_NOTICE)

    return "\n".join(lines)


def _content_for_tier_factory(
    sidecar_data: SidecarData | None,
    distill_action: str,
    *,
    stale: bool = False,
) -> Callable[[str], str]:
    """Return a content_for_tier callback for render_instruction_segment."""

    def content_for_tier(tier: str) -> str:
        if sidecar_data is None:
            return _t0_beacon(distill_action)
        if tier == "T0":
            return _t0_beacon(distill_action)
        return _t1_content(sidecar_data, stale=stale)

    return content_for_tier


# ---------------------------------------------------------------------------
# ChannelEntry factory
# ---------------------------------------------------------------------------


def build_opencode_agents_md_entry(
    *,
    tier_default: str = "T1",
    ttl_commits: int = 10,
    ttl_days: int = 7,
    quota_total_bytes: int = T1_BYTE_QUOTA,
) -> ChannelEntry:
    """Build the canonical ChannelEntry for opencode-agents-md-segment.

    Args:
        tier_default: Default render tier (T1 per PRD §2).
        ttl_commits: Staleness threshold in commits (FR08).
        ttl_days: Staleness threshold in days (FR08).
        quota_total_bytes: Maximum segment size in UTF-8 bytes (FR09).

    Returns:
        Configured ChannelEntry for use with the 11-step renderer.
    """
    return ChannelEntry(
        id="opencode-agents-md-segment",
        client="opencode",
        surface=ChannelSurface.OPENCODE_RULES_SEGMENT,
        telemetry_tag="opencode_agents_md_segment",
        file="AGENTS.md",
        lock_file=".trw/channels/agents-md.lock",
        status=ChannelStatus.ACTIVE,
        write_strategy=WriteStrategy.MARKER_REPLACE,
        tier_default=tier_default,
        tier_min="T0",
        markers=MarkersConfig(start=DISTILL_BEGIN, end=DISTILL_END),
        ttl_commits=ttl_commits,
        ttl_days=ttl_days,
        quota_total_bytes=quota_total_bytes,
        human_edit_detection=HumanEditDetection.SHA256_SEGMENT,
        cleanup=CleanupConfig(
            trigger=CleanupTrigger.TTL_EXCEEDED,
            action=CleanupAction.CLEAR_SEGMENT,
        ),
        regenerate_cmd=("trw-distill self-improve risk-report --repo . --persist-sidecar"),
        description=("opencode AGENTS.md distill segment — top-5 hotspots + top-3 conventions"),
        sidecar_schema="risk-report-sidecar/v0",
        activation_gate=None,
    )


# ---------------------------------------------------------------------------
# Sequential placement helper (ceremony section must not be disturbed)
# ---------------------------------------------------------------------------

_CEREMONY_END = "<!-- trw:end -->"


def _ensure_sequential_placement(
    agents_md_content: str,
    interior_content: str,
) -> str:
    """Inject or update the distill segment AFTER ``<!-- trw:end -->``.

    If distill markers already exist, uses idempotent replace_distill_segment.
    If absent, inserts AFTER ceremony end marker (or at EOF if no ceremony).

    Args:
        agents_md_content: Current AGENTS.md content.
        interior_content: Content to place between distill markers.

    Returns:
        Updated AGENTS.md content.
    """
    markers = MarkersConfig(start=DISTILL_BEGIN, end=DISTILL_END)

    if DISTILL_BEGIN in agents_md_content:
        return replace_distill_segment(agents_md_content, interior_content, markers=markers)

    end_idx = agents_md_content.find(_CEREMONY_END)
    if end_idx != -1:
        insert_after = end_idx + len(_CEREMONY_END)
        before = agents_md_content[:insert_after]
        after = agents_md_content[insert_after:]
        new_section = f"\n\n{DISTILL_BEGIN}\n{interior_content}\n{DISTILL_END}\n"
        return before + new_section + after.lstrip("\n")

    # Fallback: append at EOF
    return replace_distill_segment(agents_md_content, interior_content, markers=markers)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install_opencode_agents_md_distill_segment(
    repo_root: Path,
    sidecar_data: SidecarData | None,
    sidecar_sha: str | None,
    *,
    distill_action: str = ("trw-distill self-improve risk-report --repo . --persist-sidecar"),
    stale: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> InstructionSegmentResult:
    """Write the distill segment into AGENTS.md under the shared agents-md lock.

    Uses a read-merge-write pattern: reads existing AGENTS.md, injects the
    distill segment AFTER ``<!-- trw:end -->`` without touching the ceremony
    section, then writes the merged file atomically.

    Acquires ``.trw/channels/agents-md.lock`` (P0-06 audit fix) before any
    read-merge-write operation on AGENTS.md.  If the lock is already held,
    returns status="skipped_lock" without modifying the file.

    Args:
        repo_root: Repository root directory.
        sidecar_data: Parsed sidecar payload or None (renders T0 beacon).
        sidecar_sha: Git SHA of the sidecar file for TTL/state tracking.
        distill_action: Command to show when sidecar is absent (T0 beacon).
        stale: True when the sidecar SHA doesn't match HEAD (FR04).
        force: Skip TTL and conflict checks.
        dry_run: Return would-be content without writing.

    Returns:
        ``InstructionSegmentResult`` describing the outcome.
    """
    entry = build_opencode_agents_md_entry()
    channel_id = entry.id

    # Acquire shared AGENTS.md lock (P0-06 / FR05)
    try:
        lock = agents_md_lock(repo_root)
        lock.__enter__()
    except ChannelLockSkip:
        log.debug(
            "opencode_agents_md_segment_lock_skip",
            outcome="skipped_lock",
        )
        # Emit channel_lock_skip (substrate event type — not channel_conflict)
        _emit_event(channel_id, entry.client, "channel_lock_skip", None, "skipped_lock")
        return InstructionSegmentResult(
            channel_id=channel_id,
            status="skipped_lock",
        )

    try:
        return _render_and_inject_under_lock(
            entry=entry,
            repo_root=repo_root,
            sidecar_data=sidecar_data,
            sidecar_sha=sidecar_sha,
            distill_action=distill_action,
            stale=stale,
            force=force,
            dry_run=dry_run,
        )
    except Exception as exc:
        log.debug(
            "opencode_agents_md_segment_error",
            error=str(exc),
            outcome="error",
        )
        # Emit channel_error (substrate event type — not channel_conflict)
        _emit_event(channel_id, entry.client, "channel_error", None, "error")
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


def _render_and_inject_under_lock(
    *,
    entry: ChannelEntry,
    repo_root: Path,
    sidecar_data: SidecarData | None,
    sidecar_sha: str | None,
    distill_action: str,
    stale: bool,
    force: bool,
    dry_run: bool,
) -> InstructionSegmentResult:
    """Execute render-merge-write under an already-acquired agents-md lock."""
    import hashlib

    channel_id = entry.id
    content_cb = _content_for_tier_factory(sidecar_data, distill_action, stale=stale)
    tier = entry.tier_default

    # Build content with quota enforcement (FR09)
    final_content, tier_used = enforce_quota_with_tier_down(
        content="",
        current_tier=tier,
        quota_total_bytes=entry.quota_total_bytes or T1_BYTE_QUOTA,
        tier_min=entry.tier_min,
        render_at_tier=content_cb,
    )

    # Build interior = provenance + content (no markers yet)
    provenance = render_provenance_comment(
        channel_id=channel_id,
        sha=sidecar_sha or "unknown",
        ts=now_utc_iso8601(),
        tier=tier_used,
        regenerate=entry.regenerate_cmd or "",
    )
    interior = f"{provenance}\n{final_content}" if provenance else final_content

    # Read existing AGENTS.md and apply sequential placement
    target = repo_root / "AGENTS.md"
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    merged = _ensure_sequential_placement(existing, interior)

    bytes_written = len(merged.encode("utf-8"))
    tokens_estimated = len(merged.split())

    if dry_run:
        _emit_event(channel_id, entry.client, "push_ephemeral", tier_used, "dry_run")
        return InstructionSegmentResult(
            channel_id=channel_id,
            status="dry_run",
            tier_used=tier_used,
            bytes_written=bytes_written,
            tokens_estimated=tokens_estimated,
            would_write=merged,
        )

    # Write merged AGENTS.md atomically
    channels_dir = repo_root / ".trw" / "channels"
    render_log = RenderLog(channels_dir / "render-log.jsonl")
    write_atomic(
        target,
        merged,
        channel_id=channel_id,
        render_log=render_log,
        sidecar_sha=sidecar_sha,
    )

    # Persist channel state
    state_file = state_path_for(channel_id, channels_dir)
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

    _emit_event(channel_id, entry.client, "push_write", tier_used, "written", bytes_written)

    log.debug(
        "opencode_agents_md_segment_written",
        tier_used=tier_used,
        bytes_written=bytes_written,
        outcome="written",
    )
    return InstructionSegmentResult(
        channel_id=channel_id,
        status="written",
        tier_used=tier_used,
        bytes_written=bytes_written,
        tokens_estimated=tokens_estimated,
    )


def _emit_event(
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
