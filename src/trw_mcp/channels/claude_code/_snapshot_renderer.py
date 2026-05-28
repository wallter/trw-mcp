"""Tier-aware distill_snapshot.md renderer for Claude Code CC-01 channel.

Belongs to the ``channels/claude_code`` package (PRD-DIST-2405 FR12-FR13).

Produces the ``distill_snapshot.md`` content at T0/T1/T2/T3 tiers.  All
rendering uses f-strings — zero external template dependencies (NFR02).

Tier output sizes:
- T0: presence beacon, ≤ 80 chars body (excluding frontmatter)
- T1: top-1 risk file + score, top-1 convention, ≤ 600 total chars
- T2: top-5 risk files table, conventions list (≤ 5), caution dirs (≤ 3), ≤ 8192 bytes
- T3: same as T2 (T3 is the maximum tier for snapshots)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = [
    "SNAPSHOT_QUOTA_BYTES",
    "SNAPSHOT_T0_BODY_MAX_CHARS",
    "SNAPSHOT_T1_MAX_CHARS",
    "render_snapshot",
]

SNAPSHOT_QUOTA_BYTES: int = 8192
SNAPSHOT_T0_BODY_MAX_CHARS: int = 80
SNAPSHOT_T1_MAX_CHARS: int = 600


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _render_provenance_frontmatter(
    *,
    channel_id: str,
    sha: str,
    tier: str,
    regenerate_cmd: str = "trw-mcp channel-render cc-01-memory-distill-snapshot",
) -> str:
    """Render YAML frontmatter block for the snapshot file."""
    date = _utc_date()
    return (
        "---\n"
        f"# Generated: {date} | SHA: {sha[:8] if len(sha) >= 8 else sha}"
        f" | Channel: {channel_id} | Tier: {tier}\n"
        f"# Regenerate: {regenerate_cmd}\n"
        "---\n"
    )


def _render_t0_beacon(sha: str) -> str:
    """T0 presence beacon — ≤ 80 chars body, no intelligence."""
    date = _utc_date()
    body = f"TRW distill snapshot present. Date: {date}"
    return body


def _render_t1_content(
    sha: str,
    sidecar: dict[str, Any] | None,
) -> str:
    """T1 render — top-1 risk file + score, top-1 convention, ≤ 600 chars."""
    lines: list[str] = []

    if sidecar:
        risk_files: list[dict[str, Any]] = sidecar.get("risk_files", [])
        conventions: list[str] = sidecar.get("conventions", [])

        if risk_files:
            top = risk_files[0]
            score = top.get("risk_score", "?")
            path = top.get("file_path", top.get("path", "?"))
            lines.append(f"TOP RISK: {path} (score: {score})")

        if conventions:
            lines.append(f"CONVENTION: {conventions[0]}")

    if not lines:
        lines.append("No sidecar available — run trw-distill to generate intelligence.")

    return "\n".join(lines)


def _render_t2_content(
    sha: str,
    sidecar: dict[str, Any] | None,
) -> str:
    """T2 render — top-5 risk files table, conventions (≤5), caution dirs (≤3)."""
    lines: list[str] = []

    if not sidecar:
        lines.append("## Distill Intelligence\n")
        lines.append("No sidecar data available. Run: trw-distill self-improve --persist-sidecar")
        return "\n".join(lines)

    risk_files: list[dict[str, Any]] = sidecar.get("risk_files", [])[:5]
    conventions: list[str] = sidecar.get("conventions", [])[:5]
    caution_dirs: list[str] = sidecar.get("caution_directories", [])[:3]

    lines.append("## Top Risk Files\n")
    if risk_files:
        lines.append("| File | Risk Score | Caution |")
        lines.append("|------|-----------|---------|")
        for rf in risk_files:
            path = rf.get("file_path", rf.get("path", "?"))
            score = rf.get("risk_score", "?")
            caution = rf.get("caution", "")
            lines.append(f"| {path} | {score} | {caution} |")
    else:
        lines.append("No risk file data in sidecar.")

    lines.append("")
    lines.append("## Active Conventions\n")
    if conventions:
        lines.extend(f"- {conv}" for conv in conventions)
    else:
        lines.append("No convention data in sidecar.")

    lines.append("")
    lines.append("## Caution Directories\n")
    if caution_dirs:
        lines.extend(f"- {d}" for d in caution_dirs)
    else:
        lines.append("No caution directory data in sidecar.")

    return "\n".join(lines)


def render_snapshot(
    *,
    channel_id: str,
    sha: str,
    tier: str,
    sidecar: dict[str, Any] | None = None,
    regenerate_cmd: str = "trw-mcp channel-render cc-01-memory-distill-snapshot",
) -> str:
    """Render the full ``distill_snapshot.md`` content for *tier*.

    Args:
        channel_id: Channel ID for provenance frontmatter.
        sha: Git SHA of the current HEAD (or sidecar SHA).
        tier: Tier string: "T0", "T1", "T2", "T3".
        sidecar: Parsed sidecar dict, or None if unavailable.
        regenerate_cmd: Command hint written into the frontmatter.

    Returns:
        Full file content including frontmatter.
    """
    frontmatter = _render_provenance_frontmatter(
        channel_id=channel_id,
        sha=sha,
        tier=tier,
        regenerate_cmd=regenerate_cmd,
    )

    if tier == "T0":
        body = _render_t0_beacon(sha)
    elif tier == "T1":
        body = _render_t1_content(sha, sidecar)
    else:
        # T2 and T3 both use T2 content
        body = _render_t2_content(sha, sidecar)

    return frontmatter + body + "\n"
