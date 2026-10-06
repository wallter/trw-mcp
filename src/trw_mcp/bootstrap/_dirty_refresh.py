"""Refreshes of a git-dirty managed file that cost the user none of their bytes (PRD-INFRA-190-FR04 exemptions).

Belongs to ``_version_manifest.preserve_uncommitted_changes``, which puts a dirty file back whole when TRW did not
record its pre-run bytes. A file TRW only merges its own block or entry into needs no such protection: the merge
keeps everything else, so undoing the refresh protected nothing and left TRW's block stale for as long as the
file stayed uncommitted (FB-INSTALL-09: an AGENTS.md still listing about 47 tools after the server dropped to 16).
"""

from __future__ import annotations

from pathlib import Path

from ._mcp_json import mcp_json_refresh_loses_nothing


def _marker_merged_files() -> dict[str, tuple[str, str]]:
    """Repo-relative instruction files TRW merges a marker block into, with their marker pair.

    Each is written through ``guarded_instruction_write``, which keeps every byte outside the block exactly and
    backs the pre-run file up (``.trw/backups/instructions``) before writing.
    """
    from trw_mcp.state.claude_md._parser import TRW_MARKER_END, TRW_MARKER_START

    from ._antigravity_cli import _ANTIGRAVITY_TRW_END_MARKER, _ANTIGRAVITY_TRW_START_MARKER
    from ._copilot import _COPILOT_TRW_END_MARKER, _COPILOT_TRW_START_MARKER

    return {
        "AGENTS.md": (TRW_MARKER_START, TRW_MARKER_END),
        ".github/copilot-instructions.md": (_COPILOT_TRW_START_MARKER, _COPILOT_TRW_END_MARKER),
        "ANTIGRAVITY.md": (_ANTIGRAVITY_TRW_START_MARKER, _ANTIGRAVITY_TRW_END_MARKER),
    }


def refresh_changed_only_trw_block(before: str, after: str, markers: tuple[str, str] | None = None) -> bool:
    """True when *after* differs from *before* only inside TRW's block: every byte outside it is identical.

    Such a refresh costs the user nothing: the merge writer replaces the block, keeps the rest byte for byte and
    backs the old file up first, so update-project may keep it for a git-dirty instruction file. A file with no
    block yet is a pure append (or whitespace the writer replaces). Anything else, a result with no block
    included, is not provably a block refresh and the caller keeps the user's bytes.

    The raw *before* is compared, not the writer's input: healing a dead legacy block
    (``_migrate_legacy_marker_block``) removes text outside the live block, so a refresh that healed one is not
    a block-only refresh and keeps the guard.
    """
    from trw_mcp.state.claude_md._parser import TRW_MARKER_END, TRW_MARKER_START, split_around_trw_block

    pair = markers or (TRW_MARKER_START, TRW_MARKER_END)
    new_sides = split_around_trw_block(after, pair)
    if new_sides is None:
        return False
    old_sides = split_around_trw_block(before, pair)
    if old_sides is None:
        return not before.strip() or after.startswith(before)
    return old_sides == new_sides


def refresh_loses_nothing(rel: str, before: Path, after: Path, *, root: Path | None = None) -> bool:
    """True when this run's refresh of the dirty repo-relative *rel* (``before`` = pre-run, ``after`` = now) loses no user byte.

    With *root*: ``before`` is byte-for-byte what TRW last wrote to *rel* there (``_written_digests``), so an
    untracked file in a repository with no commits is TRW's own output, not an edit to keep.

    ``.mcp.json``: only TRW's own ``trw`` entry is rewritten. A marker-merged instruction file (AGENTS.md,
    ``.github/copilot-instructions.md``, ANTIGRAVITY.md): only the bytes inside TRW's markers changed. Any other
    path, and anything not provably so, is False and the caller keeps the user's bytes.
    """
    from ._written_digests import trw_wrote_these_bytes

    if root is not None and trw_wrote_these_bytes(root, rel, before):
        return True
    if rel == ".mcp.json":
        return mcp_json_refresh_loses_nothing(before)
    markers = _marker_merged_files().get(rel)
    if markers is None or before.is_symlink() or after.is_symlink():
        return False
    try:
        return refresh_changed_only_trw_block(
            before.read_bytes().decode("utf-8"), after.read_bytes().decode("utf-8"), markers
        )
    except (OSError, UnicodeDecodeError):  # trw-fail-silent-allow: unreadable means keep the guard
        return False
