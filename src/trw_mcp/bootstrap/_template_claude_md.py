"""CLAUDE.md auto-generated TRW section management.

Extracted from :mod:`trw_mcp.bootstrap._template_updater` (PRD-DIST-243
Phase 1 batch 4, cycle 32) to keep that module under the 350-effective-
LOC operator threshold. Holds the marker constants + the two helpers
that detect, replace, or append the project's auto-generated TRW
section in a CLAUDE.md file.
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "_TRW_END_MARKER",
    "_TRW_HEADER_MARKER",
    "_TRW_START_MARKER",
    "_minimal_claude_md_trw_block",
    "_update_claude_md_trw_section",
]


# CLAUDE.md markers for the auto-generated section.
_TRW_START_MARKER = "<!-- trw:start -->"
_TRW_END_MARKER = "<!-- trw:end -->"
_TRW_HEADER_MARKER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"


def _update_claude_md_trw_section(
    claude_md_path: Path,
    result: dict[str, list[str]],
) -> None:
    """Replace the auto-generated TRW section in CLAUDE.md.

    Preserves all user-written content above and below the markers.
    """
    content = claude_md_path.read_text(encoding="utf-8")
    new_block = _minimal_claude_md_trw_block()

    start_idx = content.find(_TRW_START_MARKER)
    end_idx = content.find(_TRW_END_MARKER)

    if start_idx != -1 and end_idx != -1:
        # Replace the existing auto-generated section
        end_idx += len(_TRW_END_MARKER)
        # Also capture the header marker line if present
        header_idx = content.rfind(_TRW_HEADER_MARKER, 0, start_idx)
        replace_start = header_idx if header_idx != -1 else start_idx
        updated = content[:replace_start] + new_block + content[end_idx:]
        try:
            claude_md_path.write_text(updated, encoding="utf-8")
            result["updated"].append(str(claude_md_path))
        except OSError as exc:
            result["errors"].append(f"Failed to update {claude_md_path}: {exc}")
    elif _TRW_START_MARKER not in content:
        # No TRW section -- append it
        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + new_block
        try:
            claude_md_path.write_text(content, encoding="utf-8")
            result["updated"].append(str(claude_md_path))
        except OSError as exc:
            result["errors"].append(f"Failed to update {claude_md_path}: {exc}")
    else:
        result["errors"].append("CLAUDE.md has malformed TRW markers — found start but not end")


def _minimal_claude_md_trw_block() -> str:
    """Return just the auto-generated TRW section for CLAUDE.md updates."""
    import sys

    # Look up _minimal_claude_md via the package module so that
    # patch("trw_mcp.bootstrap._minimal_claude_md", ...) in tests
    # correctly intercepts the call.
    bootstrap_pkg = sys.modules["trw_mcp.bootstrap"]
    full: str = bootstrap_pkg._minimal_claude_md()
    start_idx = full.find(_TRW_HEADER_MARKER)
    end_idx = full.find(_TRW_END_MARKER)
    if start_idx != -1 and end_idx != -1:
        return str(full[start_idx : end_idx + len(_TRW_END_MARKER)]) + "\n"
    # Fallback: return entire trw:start..trw:end
    start_idx = full.find(_TRW_START_MARKER)
    if start_idx != -1 and end_idx != -1:
        return str(full[start_idx : end_idx + len(_TRW_END_MARKER)]) + "\n"
    return ""
