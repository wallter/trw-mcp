"""Canonical provenance comment/frontmatter renderer.

All channel renderers call render_provenance_comment() and prepend the
result to their channel content (PRD-DIST-2400 FR19).

Single-line variants from earlier plans are deprecated; this multiline
format is the only canonical form.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

__all__ = [
    "now_utc_iso8601",
    "parse_provenance_comment",
    "render_provenance_comment",
    "render_provenance_frontmatter",
]

_PROVENANCE_BLOCK_RE = re.compile(
    r"<!-- TRW:PROVENANCE\n(.*?)-->",
    re.DOTALL,
)

_FIELD_RE = re.compile(r"^(\w+): (.+)$")


def now_utc_iso8601() -> str:
    """Return UTC timestamp with millisecond precision and Z suffix.

    Example: ``2026-05-28T12:34:56.789Z``
    """
    now = datetime.now(tz=timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def render_provenance_comment(
    channel_id: str,
    sha: str,
    ts: str,
    tier: str,
    regenerate: str,
) -> str:
    """Render the canonical multiline TRW:PROVENANCE HTML comment block.

    All downstream renderers MUST call this and prepend the result to
    channel content.

    Returns a string of the form::

        <!-- TRW:PROVENANCE
        generated_by: trw-mcp
        channel_id: <channel_id>
        sha: <sha>
        ts: <ts>
        tier: <tier>
        regenerate: <regenerate>
        -->

    The trailing newline is included so callers can concatenate directly.
    """
    return (
        "<!-- TRW:PROVENANCE\n"
        f"generated_by: trw-mcp\n"
        f"channel_id: {channel_id}\n"
        f"sha: {sha}\n"
        f"ts: {ts}\n"
        f"tier: {tier}\n"
        f"regenerate: {regenerate}\n"
        "-->\n"
    )


def render_provenance_frontmatter(
    channel_id: str,
    sha: str,
    ts: str,
    tier: str,
    regenerate: str,
    *,
    description: str | None = None,
    globs: str | None = None,
    always_apply: bool = False,
) -> str:
    """Render YAML frontmatter + provenance comment for MDC files (Cursor).

    Returns a string with YAML frontmatter block (description, globs,
    alwaysApply) followed immediately by the provenance comment.

    The caller is responsible for appending the channel body content after.
    """
    desc_line = f"description: {description}" if description else "description: ''"
    globs_line = f"globs: {globs}" if globs else "globs: ''"
    always_line = f"alwaysApply: {'true' if always_apply else 'false'}"

    frontmatter = f"---\n{desc_line}\n{globs_line}\n{always_line}\n---\n"
    provenance = render_provenance_comment(
        channel_id=channel_id,
        sha=sha,
        ts=ts,
        tier=tier,
        regenerate=regenerate,
    )
    return frontmatter + provenance


def parse_provenance_comment(content: str) -> dict[str, str] | None:
    """Extract provenance fields from *content*.

    Returns a dict of field names → values, or None if no block is found.

    Fields parsed: ``generated_by``, ``channel_id``, ``sha``, ``ts``,
    ``tier``, ``regenerate`` (plus any additional fields present).
    """
    match = _PROVENANCE_BLOCK_RE.search(content)
    if not match:
        return None

    body = match.group(1)
    result: dict[str, str] = {}
    for line in body.splitlines():
        field_match = _FIELD_RE.match(line.strip())
        if field_match:
            result[field_match.group(1)] = field_match.group(2).strip()

    return result or None
