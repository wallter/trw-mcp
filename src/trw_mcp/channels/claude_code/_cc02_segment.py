"""Claude Code CC-02 channel: CLAUDE.md distill segment.

Belongs to the ``channels/claude_code`` package (PRD-DIST-2405 FR19-FR24).

Installs and manages the ``<!-- trw-distill:start --> / <!-- trw-distill:end -->``
segment in ``CLAUDE.md``.  The segment is a SIBLING of the TRW ceremony
section (``<!-- trw:start --> / <!-- trw:end -->``), placed immediately after
``<!-- trw:end -->`` — NEVER nested inside it.

Tier defaults:
- T0: Presence beacon only (≤ 80 chars body)
- T1 (default): Top-3 high-churn directories, top-2 DO-NOT-REMOVE
  marker locations, top-1 active convention (≤ 150 tokens / ~600 chars)
- T2/T3: Extended content (quota gated to 3072 bytes)

Conflict detection uses SHA256_SEGMENT mode from ``_conflict.py``.
SHA computation excludes the generated metadata comment line to avoid
false positives from timestamp-only changes (FR20).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
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
from trw_mcp.channels.instruction_segment import (
    InstructionSegmentResult,
    render_instruction_segment,
)

log = structlog.get_logger(__name__)

__all__ = [
    "CC02_MARKER_END",
    "CC02_MARKER_START",
    "CC02_QUOTA_BYTES",
    "build_cc02_channel_entry",
    "install_cc02_segment",
    "render_cc02_segment",
    "update_cc02_segment",
]

CC02_MARKER_START: str = "<!-- trw-distill:start -->"
CC02_MARKER_END: str = "<!-- trw-distill:end -->"
CC02_QUOTA_BYTES: int = 3072

_CHANNEL_ID: str = "cc-02-claude-md-distill-segment"
_TTL_COMMITS: int = 10
_TTL_DAYS: int = 3

# Token budgets per tier
_T0_MAX_CHARS: int = 80
_T1_MAX_CHARS: int = 600  # ~150 tokens


def _utc_date() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _render_metadata_comment(*, sha: str, commits_since: int | None) -> str:
    """Render the HTML comment metadata line (FR20).

    Excludes time component — date-only for SHA stability.
    """
    commits_str = str(commits_since) if commits_since is not None else "?"
    return (
        f"<!-- Generated: {_utc_date()} | SHA: {sha[:8] if len(sha) >= 8 else sha}"
        f" | Commits-since: {commits_str}"
        " | Regenerate: trw-mcp channel-render cc-02-claude-md-distill-segment -->"
    )


def _render_t0_beacon() -> str:
    return "TRW distill intelligence available. Run `trw-mcp channel-render cc-02-claude-md-distill-segment` to refresh."


def _render_t1_content(
    sha: str,
    sidecar: dict[str, Any] | None,
    commits_since: int | None = None,
) -> str:
    """T1 render — top-3 high-churn dirs, top-2 DO-NOT-REMOVE, top-1 convention.

    Total ≤ 150 tokens (~600 chars).
    """
    lines: list[str] = [_render_metadata_comment(sha=sha, commits_since=commits_since), ""]

    if not sidecar:
        lines.append("No distill sidecar available. Run trw-distill to generate intelligence.")
        return "\n".join(lines)

    # Top-3 high-churn directories
    churn_dirs: list[str] = sidecar.get("high_churn_directories", [])[:3]
    if churn_dirs:
        lines.append("**High-churn directories** (caution when editing):")
        for d in churn_dirs:
            lines.append(f"- {d}")
        lines.append("")

    # Top-2 DO-NOT-REMOVE marker locations
    dnr_files: list[dict[str, Any]] = sidecar.get("do_not_remove_markers", [])[:2]
    if dnr_files:
        lines.append("**DO-NOT-REMOVE markers:**")
        for dnr in dnr_files:
            path = dnr.get("file_path", dnr.get("path", "?"))
            lines.append(f"- {path}")
        lines.append("")

    # Top-1 active convention
    conventions: list[str] = sidecar.get("conventions", [])
    if conventions:
        lines.append(f"**Convention:** {conventions[0]}")

    content = "\n".join(lines)

    # Enforce T1 character budget
    if len(content) > _T1_MAX_CHARS:
        content = content[:_T1_MAX_CHARS] + "\n... (truncated)"

    return content


def _render_t2_content(
    sha: str,
    sidecar: dict[str, Any] | None,
    commits_since: int | None = None,
) -> str:
    """T2/T3 render — extended content, quota-gated at 3072 bytes."""
    lines: list[str] = [_render_metadata_comment(sha=sha, commits_since=commits_since), ""]

    if not sidecar:
        lines.append("No distill sidecar available. Run trw-distill to generate intelligence.")
        return "\n".join(lines)

    risk_files: list[dict[str, Any]] = sidecar.get("risk_files", [])[:5]
    if risk_files:
        lines.append("**Top risk files:**")
        lines.append("| File | Score |")
        lines.append("|------|-------|")
        for rf in risk_files:
            path = rf.get("file_path", rf.get("path", "?"))
            score = rf.get("risk_score", "?")
            lines.append(f"| {path} | {score} |")
        lines.append("")

    conventions: list[str] = sidecar.get("conventions", [])[:3]
    if conventions:
        lines.append("**Conventions:**")
        for conv in conventions:
            lines.append(f"- {conv}")

    return "\n".join(lines)


def build_cc02_channel_entry() -> ChannelEntry:
    """Build the ``ChannelEntry`` descriptor for the CC-02 channel."""
    return ChannelEntry(
        id=_CHANNEL_ID,
        client="claude-code",
        surface=ChannelSurface.INSTRUCTION_FILE_SEGMENT,
        telemetry_tag="cc02_claude_md_segment",
        file="CLAUDE.md",
        status=ChannelStatus.ACTIVE,
        write_strategy=WriteStrategy.MARKER_REPLACE,
        tier_default="T1",
        tier_min="T0",
        markers=MarkersConfig(start=CC02_MARKER_START, end=CC02_MARKER_END),
        ttl_commits=_TTL_COMMITS,
        ttl_days=_TTL_DAYS,
        quota_total_bytes=CC02_QUOTA_BYTES,
        human_edit_detection=HumanEditDetection.SHA256_SEGMENT,
        regenerate_cmd="trw-mcp channel-render cc-02-claude-md-distill-segment",
        cleanup=CleanupConfig(
            trigger=CleanupTrigger.NONE,
            action=CleanupAction.NONE,
        ),
    )


def render_cc02_segment(
    *,
    sha: str,
    tier: str = "T1",
    sidecar: dict[str, Any] | None = None,
    commits_since: int | None = None,
) -> str:
    """Render the CC-02 CLAUDE.md segment content for *tier*.

    Args:
        sha: Git HEAD SHA (for provenance comment).
        tier: Tier string ("T0", "T1", "T2", "T3").
        sidecar: Parsed sidecar dict, or None.
        commits_since: Commits since last render (for metadata comment).

    Returns:
        Rendered segment content (without markers — caller wraps).
    """
    if tier == "T0":
        return _render_t0_beacon()
    elif tier == "T1":
        return _render_t1_content(sha, sidecar, commits_since)
    else:
        return _render_t2_content(sha, sidecar, commits_since)


def install_cc02_segment(
    *,
    repo_root: Path,
    sha: str = "unknown",
    sidecar: dict[str, Any] | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> InstructionSegmentResult:
    """Install the CC-02 segment into ``CLAUDE.md``.

    Uses the shared 11-step renderer from ``instruction_segment/_renderer.py``.

    Args:
        repo_root: Repository root directory.
        sha: Git HEAD SHA.
        sidecar: Parsed sidecar dict, or None.
        force: Bypass TTL and conflict detection.
        dry_run: Return would-be content without writing.

    Returns:
        ``InstructionSegmentResult`` describing the outcome.
    """
    entry = build_cc02_channel_entry()

    def content_for_tier(tier: str) -> str:
        return render_cc02_segment(sha=sha, tier=tier, sidecar=sidecar)

    return render_instruction_segment(
        entry=entry,
        repo_root=repo_root,
        sidecar_sha=sha if sha != "unknown" else None,
        content_for_tier=content_for_tier,
        force=force,
        dry_run=dry_run,
    )


def update_cc02_segment(
    *,
    repo_root: Path,
    sha: str = "unknown",
    sidecar: dict[str, Any] | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> InstructionSegmentResult:
    """Refresh the CC-02 CLAUDE.md segment (TTL-checked unless force=True).

    Used by ``update-project --client claude-code`` (FR23).

    Args:
        repo_root: Repository root directory.
        sha: Git HEAD SHA.
        sidecar: Parsed sidecar dict, or None.
        force: Bypass TTL and conflict detection.
        dry_run: Return would-be content without writing.

    Returns:
        ``InstructionSegmentResult`` describing the outcome.
    """
    return install_cc02_segment(
        repo_root=repo_root,
        sha=sha,
        sidecar=sidecar,
        force=force,
        dry_run=dry_run,
    )
