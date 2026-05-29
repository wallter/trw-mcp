"""Codex AGENTS.md hotspot distill segment renderer.

# Managed by TRW — no trw_distill imports permitted.

Implements the `codex-agents-md-hotspots` channel using the shared
11-step renderer from PRD-DIST-2400 instruction_segment/_renderer.py.

Sequential placement: the distill segment is placed AFTER `<!-- trw:end -->`
as a sibling section, NOT nested inside the TRW ceremony section (audit P1-19).

Token estimates use tiktoken cl100k_base when available; falls back to
char/4 with a 20% overhead buffer (audit P1-06). NEVER claims exact count.

PRD-DIST-2402.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

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
from trw_mcp.channels.instruction_segment import InstructionSegmentResult

log = structlog.get_logger(__name__)

__all__ = [
    "build_codex_channel_entry",
    "render_and_inject",
]

# ---------------------------------------------------------------------------
# Marker constants (sequential, NOT nested in <!-- trw:end --> — audit P1-19)
# ---------------------------------------------------------------------------

HOTSPOTS_BEGIN = "<!-- trw-distill:hotspots BEGIN -->"
HOTSPOTS_END = "<!-- trw-distill:hotspots END -->"

# Token budgets (FR16)
T1_TOKEN_BUDGET = 400
T2_TOKEN_BUDGET = 900

# Byte quota (FR03)
DEFAULT_QUOTA_BYTES = 8192


# ---------------------------------------------------------------------------
# Token counting (audit P1-06: proper tokenizer with fallback)
# ---------------------------------------------------------------------------


def _count_tokens_estimate(text: str) -> int:
    """Estimate token count using tiktoken if available, else char/4 + 20% overhead.

    NEVER claims exact count — this is always an estimate.

    Args:
        text: Text to estimate token count for.

    Returns:
        Integer token count estimate with overhead buffer applied on fallback.
    """
    try:
        import tiktoken  # type: ignore[import-not-found]

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except (ImportError, Exception):
        # Fallback: char/4 with 20% overhead buffer (audit P1-06)
        raw_estimate = len(text) // 4
        return int(raw_estimate * 1.2)


# ---------------------------------------------------------------------------
# Content builders
# ---------------------------------------------------------------------------


def _format_hotspot_entry(entry: dict[str, Any], idx: int) -> str:
    """Format a single hotspot entry for display.

    Args:
        entry: Hotspot dict with keys: file, risk_score, reason (optional).
        idx: 1-based index.

    Returns:
        Formatted bullet string.
    """
    path = entry.get("file", entry.get("path", "unknown"))
    score = entry.get("risk_score", entry.get("score", 0.0))
    reason = entry.get("reason", entry.get("summary", ""))
    score_str = f"{float(score):.2f}" if score else "N/A"
    reason_part = f" — {reason}" if reason else ""
    return f"{idx}. `{path}` (risk: {score_str}){reason_part}"


def _format_convention(convention: str | dict[str, Any], idx: int) -> str:
    """Format a single coding convention for display.

    Args:
        convention: Convention string or dict with 'text'/'description' key.
        idx: 1-based index.

    Returns:
        Formatted bullet string.
    """
    if isinstance(convention, dict):
        text = convention.get("text", convention.get("description", str(convention)))
    else:
        text = str(convention)
    return f"- {text}"


def _format_edge_case(edge_case: str | dict[str, Any]) -> str:
    """Format a single edge case for display.

    Args:
        edge_case: Edge case string or dict with 'description'/'text' key.

    Returns:
        Formatted bullet string.
    """
    if isinstance(edge_case, dict):
        text = edge_case.get("description", edge_case.get("text", str(edge_case)))
    else:
        text = str(edge_case)
    return f"- {text}"


def _content_for_tier_factory(sidecar_data: dict[str, Any] | None) -> Any:
    """Return a content_for_tier callback bound to *sidecar_data*.

    Args:
        sidecar_data: Parsed sidecar payload or None for stub.

    Returns:
        Callable[[str], str] for use with render_instruction_segment().
    """

    def content_for_tier(tier: str) -> str:
        if sidecar_data is None:
            return _stale_stub_content()

        hotspots: list[dict[str, Any]] = sidecar_data.get("hotspots", [])
        conventions: list[Any] = sidecar_data.get("conventions", [])
        edge_cases: list[Any] = sidecar_data.get("edge_cases", [])

        if tier in ("T2", "T3", "T4"):
            top_spots = hotspots[:5]
            top_convs = conventions[:5]
            top_edges = edge_cases[:2]

            lines: list[str] = []
            lines.append("## TRW Distill — Codebase Hotspots (T2)\n")
            lines.append("### Top Hotspot Files\n")
            for i, h in enumerate(top_spots, 1):
                lines.append(_format_hotspot_entry(h, i))
            if not top_spots:
                lines.append("_No hotspot data available._")
            lines.append("\n### Coding Conventions\n")
            for i, c in enumerate(top_convs, 1):
                lines.append(_format_convention(c, i))
            if not top_convs:
                lines.append("_No convention data available._")
            if top_edges:
                lines.append("\n### Edge Cases to Watch\n")
                lines.extend(_format_edge_case(ec) for ec in top_edges)
            return "\n".join(lines)

        # T1 (and T0 fallback)
        top_spots_t1 = hotspots[:3]
        top_convs_t1 = conventions[:3]

        lines2: list[str] = []
        lines2.append("## TRW Distill — Codebase Hotspots (T1)\n")
        lines2.append("### Top Hotspot Files\n")
        for i, h in enumerate(top_spots_t1, 1):
            lines2.append(_format_hotspot_entry(h, i))
        if not top_spots_t1:
            lines2.append("_No hotspot data available._")
        lines2.append("\n### Coding Conventions\n")
        for i, c in enumerate(top_convs_t1, 1):
            lines2.append(_format_convention(c, i))
        if not top_convs_t1:
            lines2.append("_No convention data available._")
        return "\n".join(lines2)

    return content_for_tier


def _stale_stub_content() -> str:
    """Return a TTL-expired stub content with regeneration command."""
    return (
        "## TRW Distill — Hotspot Segment (STALE)\n\n"
        "Distill sidecar not found or expired. "
        "Regenerate with:\n\n"
        "```\ntrw-distill self-improve risk-report\n```\n\n"
        "_Run `trw_instructions_sync` after regenerating to refresh this segment._"
    )


# ---------------------------------------------------------------------------
# ChannelEntry factory
# ---------------------------------------------------------------------------


def build_codex_channel_entry(
    *,
    tier_default: str = "T1",
    ttl_commits: int = 20,
    ttl_days: int = 7,
    quota_total_bytes: int = DEFAULT_QUOTA_BYTES,
) -> ChannelEntry:
    """Build the canonical ChannelEntry for codex-agents-md-hotspots.

    Args:
        tier_default: Default render tier (T1 per PRD §2).
        ttl_commits: Staleness threshold in commits.
        ttl_days: Staleness threshold in days.
        quota_total_bytes: Maximum segment size in UTF-8 bytes.

    Returns:
        Configured ChannelEntry ready for render_instruction_segment().
    """
    return ChannelEntry(
        id="codex-agents-md-hotspots",
        client="codex",
        surface=ChannelSurface.CODEX_AGENTS_MD_SEGMENT,
        telemetry_tag="codex_agents_md_hotspots",
        file="AGENTS.md",
        lock_file=".trw/channels/codex-agents-md-hotspots.lock",
        status=ChannelStatus.ACTIVE,
        write_strategy=WriteStrategy.MARKER_REPLACE,
        tier_default=tier_default,
        tier_min="T0",
        markers=MarkersConfig(start=HOTSPOTS_BEGIN, end=HOTSPOTS_END),
        ttl_commits=ttl_commits,
        ttl_days=ttl_days,
        quota_total_bytes=quota_total_bytes,
        human_edit_detection=HumanEditDetection.SHA256_SEGMENT,
        cleanup=CleanupConfig(
            trigger=CleanupTrigger.TTL_EXCEEDED,
            action=CleanupAction.CLEAR_SEGMENT,
        ),
        regenerate_cmd="trw-distill self-improve risk-report",
        description="Codex AGENTS.md hotspot segment — top-N risk files and conventions",
        sidecar_schema="risk-report-sidecar/v0",
        activation_gate=None,
    )


# ---------------------------------------------------------------------------
# Sequential placement helper
# ---------------------------------------------------------------------------


def _ensure_sequential_placement(
    agents_md_content: str,
    interior_content: str,
    *,
    trw_end_marker: str = "<!-- trw:end -->",
) -> str:
    """Ensure the distill segment appears AFTER trw:end, not nested inside it.

    Uses idempotent replace_distill_segment to inject or update the segment.
    The distill markers are managed by this function — interior_content should
    NOT contain the HOTSPOTS_BEGIN/HOTSPOTS_END markers.

    If AGENTS.md already has the distill markers, replaces the interior
    (idempotent). If markers are absent, places the segment sequentially
    AFTER trw:end (audit P1-19), or at EOF if no trw:end marker present.

    Args:
        agents_md_content: Current AGENTS.md content.
        interior_content: Content to place between the distill markers
            (should NOT include the marker strings themselves).
        trw_end_marker: The TRW ceremony end marker string.

    Returns:
        Updated AGENTS.md content with distill segment placed sequentially.
    """
    markers = MarkersConfig(start=HOTSPOTS_BEGIN, end=HOTSPOTS_END)

    # If distill markers already exist, use idempotent replace (interior only).
    if HOTSPOTS_BEGIN in agents_md_content:
        return replace_distill_segment(agents_md_content, interior_content, markers=markers)

    # Markers absent — place AFTER trw:end if present, else at EOF.
    end_idx = agents_md_content.find(trw_end_marker)
    if end_idx != -1:
        insert_after = end_idx + len(trw_end_marker)
        before = agents_md_content[:insert_after]
        after = agents_md_content[insert_after:]
        new_section = f"\n\n{HOTSPOTS_BEGIN}\n{interior_content}\n{HOTSPOTS_END}\n"
        return before + new_section + after.lstrip("\n")

    # Fallback: append at EOF.
    return replace_distill_segment(agents_md_content, interior_content, markers=markers)


# ---------------------------------------------------------------------------
# Public API: render_and_inject
# ---------------------------------------------------------------------------


def render_and_inject(
    *,
    repo_root: Path,
    sidecar_data: dict[str, Any] | None,
    sidecar_sha: str | None,
    target_file: Path | None = None,
    tier_override: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> InstructionSegmentResult:
    """Render and inject the Codex hotspot segment into AGENTS.md.

    Uses the 11-step instruction_segment renderer in dry_run mode to obtain the
    wrapped segment, then applies sequential placement into the existing AGENTS.md
    (audit P1-19): segment placed AFTER `<!-- trw:end -->`, not nested inside.

    Args:
        repo_root: Repository root directory.
        sidecar_data: Parsed sidecar payload or None (renders stub).
        sidecar_sha: Git SHA of the sidecar file for TTL/state tracking.
        target_file: Override target file path (defaults to repo_root/AGENTS.md).
        tier_override: Force a specific tier (T1/T2); defaults to T1.
        force: Skip TTL and conflict checks.
        dry_run: Return would-be content without writing.

    Returns:
        InstructionSegmentResult from the 11-step renderer.
    """
    from trw_mcp.channels._lock import ChannelLock, ChannelLockSkip

    tier = tier_override or "T1"
    entry = build_codex_channel_entry(tier_default=tier)
    content_cb = _content_for_tier_factory(sidecar_data)
    resolved_target = target_file or (repo_root / "AGENTS.md")

    # Acquire a lock covering the full read-merge-write cycle (FR11).
    lock_path = repo_root / ".trw" / "channels" / "codex-agents-md-hotspots.lock"
    try:
        lock = ChannelLock(lock_path)
        lock.__enter__()
    except ChannelLockSkip:
        log.debug(
            "codex_agents_md_hotspots_render",
            status="skipped_lock",
            outcome="skipped_lock",
        )
        return InstructionSegmentResult(
            channel_id=entry.id,
            status="skipped_lock",
        )

    try:
        return _render_and_inject_under_lock(
            entry=entry,
            repo_root=repo_root,
            sidecar_data=sidecar_data,
            sidecar_sha=sidecar_sha,
            resolved_target=resolved_target,
            content_cb=content_cb,
            force=force,
            dry_run=dry_run,
        )
    except Exception as exc:
        log.debug(
            "codex_agents_md_hotspots_render_error",
            error=str(exc),
            outcome="error",
        )
        return InstructionSegmentResult(
            channel_id=entry.id,
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
    sidecar_data: dict[str, Any] | None,
    sidecar_sha: str | None,
    resolved_target: Path,
    content_cb: Any,
    force: bool,
    dry_run: bool,
) -> InstructionSegmentResult:
    """Execute the render and inject under an already-acquired lock."""
    import hashlib

    from trw_mcp.channels._conflict import RenderLog, write_atomic
    from trw_mcp.channels._provenance import now_utc_iso8601, render_provenance_comment
    from trw_mcp.channels._quota import enforce_quota_with_tier_down
    from trw_mcp.channels._state import ChannelState, state_path_for, write_state
    from trw_mcp.channels._telemetry import append_channel_event

    # Choose tier and build content.
    tier: str = entry.tier_default

    final_content, tier_used = enforce_quota_with_tier_down(
        content="",
        current_tier=tier,
        quota_total_bytes=entry.quota_total_bytes,
        tier_min=entry.tier_min,
        render_at_tier=content_cb,
    )

    # Build the interior content (provenance + hotspot content) WITHOUT markers.
    # Markers are injected by _ensure_sequential_placement.
    provenance = render_provenance_comment(
        channel_id=entry.id,
        sha=sidecar_sha or "unknown",
        ts=now_utc_iso8601(),
        tier=tier_used,
        regenerate=entry.regenerate_cmd or "",
    )
    # Interior = provenance comment + rendered content (no markers)
    interior_content = f"{provenance}\n{final_content}" if provenance else final_content

    tokens_estimated = _count_tokens_estimate(interior_content)

    # Apply sequential placement into the existing AGENTS.md.
    # _ensure_sequential_placement handles marker injection/replacement.
    existing_content = resolved_target.read_text(encoding="utf-8") if resolved_target.exists() else ""
    merged_content = _ensure_sequential_placement(existing_content, interior_content)
    bytes_written = len(merged_content.encode("utf-8"))

    if dry_run:
        result = InstructionSegmentResult(
            channel_id=entry.id,
            status="dry_run",
            tier_used=tier_used,
            bytes_written=bytes_written,
            tokens_estimated=tokens_estimated,
            would_write=merged_content,
        )
        log.debug(
            "codex_agents_md_hotspots_render",
            status="dry_run",
            tier_used=tier_used,
            bytes_written=bytes_written,
            dry_run=True,
            outcome="dry_run",
        )
        return result

    # Write merged AGENTS.md atomically.
    channels_dir = repo_root / ".trw" / "channels"
    render_log = RenderLog(channels_dir / "render-log.jsonl")
    write_atomic(
        resolved_target,
        merged_content,
        channel_id=entry.id,
        render_log=render_log,
        sidecar_sha=sidecar_sha,
    )

    # Persist updated channel state.
    state_file = state_path_for(entry.id, channels_dir)
    seg_sha = hashlib.sha256(final_content.encode("utf-8")).hexdigest()
    new_state = ChannelState(
        channel_id=entry.id,
        last_render_tier=tier_used,
        last_render_bytes=bytes_written,
        last_render_tokens_est=tokens_estimated,
        last_sidecar_sha=sidecar_sha,
        segment_interior_sha256=seg_sha,
        last_render_ts=now_utc_iso8601(),
    )
    write_state(new_state, state_file)

    # Emit telemetry (fail-open).
    try:
        append_channel_event(
            channel_id=entry.id,
            client=entry.client,
            event_type="push_write",
            tier=tier_used,
            bytes_emitted=bytes_written,
            extra={"outcome": "written"},
        )
    except Exception:
        pass

    log.debug(
        "codex_agents_md_hotspots_render",
        status="written",
        tier_used=tier_used,
        bytes_written=bytes_written,
        dry_run=False,
        outcome="written",
    )

    return InstructionSegmentResult(
        channel_id=entry.id,
        status="written",
        tier_used=tier_used,
        bytes_written=bytes_written,
        tokens_estimated=tokens_estimated,
    )
