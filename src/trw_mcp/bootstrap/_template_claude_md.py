"""Client-record helpers and the claude-code instruction carriers for bootstrap.

Claude Code reads ``AGENTS.md`` natively, so claude-code shares the one ``AGENTS.md`` carrier the other clients
use, and TRW never creates a ``CLAUDE.md``. But Claude Code skips ``AGENTS.md`` while a ``CLAUDE.md`` exists, so
when the project has its own ``CLAUDE.md`` TRW keeps one small marked block in it that imports TRW's context
(:func:`link_claude_md`). Neither file is ever deleted, moved or synced into the other. This module also holds the
recorded-client helpers the bootstrap writers share.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import NamedTuple

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "_TRW_END_MARKER",
    "_TRW_HEADER_MARKER",
    "_TRW_START_MARKER",
    "claude_code_is_claimed",
    "link_claude_md",
    "write_claude_code_agents_md",
]


# Instruction-file markers for the auto-generated section.
_TRW_START_MARKER = "<!-- trw:start -->"
_TRW_END_MARKER = "<!-- trw:end -->"
_TRW_HEADER_MARKER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"


def claude_code_is_claimed(project_root: Path, ide_targets: list[str] | None = None) -> bool:
    """Return whether claude-code is one of this project's install targets.

    *ide_targets* is authoritative when the caller already resolved it;
    otherwise the record answers, and detection only as a fallback, because
    post-install detection cannot distinguish TRW's own ``.claude/`` from the
    user's.
    """
    from trw_mcp.state.claude_md._orphan_strip import _claude_code_claimed

    if ide_targets is not None:
        return _claude_code_claimed(ide_targets, from_record=True)
    recorded = _recorded_targets(project_root)
    if recorded:
        return _claude_code_claimed(recorded, from_record=True)
    return _claude_code_claimed(_recorded_or_detected_targets(project_root))


def write_claude_code_agents_md(target_dir: Path, result: dict[str, list[str]]) -> None:
    """Write claude-code's TRW block into the shared ``AGENTS.md``.

    Same guarded marker-merge writer the other AGENTS.md clients use; user
    content outside the markers is preserved. Never forced: ``force`` there
    replaces a hand-written file wholesale, which ``init --force`` must not do.
    """
    from ._opencode import generate_agents_md

    written = generate_agents_md(target_dir, client_id="claude-code")
    for key in ("created", "updated", "errors", "warnings"):  # warnings: the named copy of a previous version
        for entry in written.get(key, []):
            if entry not in result.setdefault(key, []):
                result[key].append(entry)


def link_claude_md_after_update(target_dir: Path, result: dict[str, list[str]], *, dry_run: bool = False) -> None:
    """update-project's CLAUDE.md step, run once the transaction committed (operator P0, 2026-10-01).

    Outside the transaction on purpose: a rolled-back update never touches the user's CLAUDE.md, and a CLAUDE.md
    symlink (a common link to AGENTS.md) never fails the surface check. Only when Claude Code is one of this
    project's clients, by the same claim rule claude-code's AGENTS.md write uses (codex S1 r1). A preview whose
    scratch run failed shows nothing.
    """
    if not (dry_run and result["errors"]) and claude_code_is_claimed(target_dir):
        link_claude_md(target_dir, result, dry_run=dry_run)


def link_claude_md(target_dir: Path, result: dict[str, list[str]], *, dry_run: bool = False) -> None:
    """Keep TRW's context reachable from the project's EXISTING root ``CLAUDE.md`` (operator P0, 2026-10-01).

    Adds TRW's marked block (``claude_md_link_section``: the ``.trw/INSTRUCTIONS.md`` import and a plain-text
    pointer to ``AGENTS.md``) or replaces the one already there -- a pre-8.0 TRW block, its promoted learnings
    included, is TRW-marked content. Every byte outside the markers stays as the user wrote it, line endings
    included. Never creates a ``CLAUDE.md``, never writes through a symlink, and leaves a file with unbalanced or
    duplicate markers untouched with a warning. The write goes through the instruction guard (backup first, shrink
    floors); a refusal is a warning, because this runs after the update committed. *dry_run* only reports.
    """
    path = target_dir / "CLAUDE.md"
    if path.is_symlink():
        result.setdefault("preserved", []).append("CLAUDE.md (symlink, not followed)")
        return
    if not os.path.lexists(path):
        return
    from trw_mcp.state.claude_md._exact_text import append_block, block_text, file_eol
    from trw_mcp.state.claude_md._instructions_link import claude_md_link_section, fenced_line_indices

    from ._file_ops import has_marker, replace_marker_region

    content = _read_claude_md(path)
    if not isinstance(content, str):
        result.setdefault("warnings", []).append(f"CLAUDE.md left untouched: {content.reason}")
        return
    markers = {_TRW_START_MARKER, _TRW_END_MARKER}
    fenced = fenced_line_indices(content)
    if any(i in fenced and line.strip() in markers for i, line in enumerate(content.splitlines())):
        result.setdefault("warnings", []).append(
            "CLAUDE.md left untouched: a trw:start/trw:end line sits inside a fenced code block, so TRW cannot tell "
            "its own block from your example. Move the example out of the fence or re-run after editing it."
        )
        return
    block = claude_md_link_section()
    updated = replace_marker_region(
        content, start=_TRW_START_MARKER, end=_TRW_END_MARKER, new_block=block, header=_TRW_HEADER_MARKER
    )
    if updated is None:
        if has_marker(content, (_TRW_START_MARKER, "start"), (_TRW_END_MARKER, "end")):
            result.setdefault("warnings", []).append(
                "CLAUDE.md left untouched: its TRW markers are unbalanced or duplicated, so TRW cannot tell its own "
                "block from your text. Remove the stray trw:start/trw:end lines and re-run update-project."
            )
            return
        if content and not content.strip():
            # Whitespace-only, but still the user's bytes: keep them and append (append_block would replace them).
            eol = file_eol(content)
            updated = content + ("" if content.endswith(("\n", "\r")) else eol) + block_text(block, eol)
        else:
            updated = append_block(content, block)
    if updated == content:
        return
    if dry_run:
        result.setdefault("updated", []).append("CLAUDE.md")
        return
    from ._guarded_write import guarded_bootstrap_write

    outcome: dict[str, list[str]] = {"created": [], "updated": [], "errors": []}
    if guarded_bootstrap_write(
        path,
        updated,
        project_root=target_dir,
        markers=(_TRW_START_MARKER, _TRW_END_MARKER),
        result=outcome,
        rel_path="CLAUDE.md",
        expected_current=content,
    ):
        result.setdefault("updated", []).append("CLAUDE.md")
        result.setdefault("warnings", []).extend(outcome.get("warnings", []))  # names the previous version's copy
        return
    result.setdefault("warnings", []).extend(f"CLAUDE.md left untouched: {error}" for error in outcome["errors"])


#: A root CLAUDE.md is a few KiB; anything past this is not read (and so never rewritten).
_MAX_CLAUDE_MD = 4 << 20


class _Unreadable(NamedTuple):
    reason: str


def _read_claude_md(path: Path) -> str | _Unreadable:
    """*path*'s text with no newline translation, or why it was not read.

    Opened without following a link and without blocking (a FIFO swapped in never stalls an update), only a
    regular file, and only up to ``_MAX_CLAUDE_MD`` bytes (codex S1 r1). The descriptor closes on every path.
    """
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:  # trw-fail-silent-allow: the reason is returned and reported as a warning
        return _Unreadable(f"it could not be opened ({exc})")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return _Unreadable("it is not a regular file")
        if info.st_size > _MAX_CLAUDE_MD:
            return _Unreadable(f"it is larger than {_MAX_CLAUDE_MD} bytes")
        chunks: list[bytes] = []
        remaining = _MAX_CLAUDE_MD + 1
        while remaining > 0 and (chunk := os.read(fd, remaining)):
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    except OSError as exc:  # trw-fail-silent-allow: the reason is returned and reported as a warning
        return _Unreadable(f"it could not be read ({exc})")
    finally:
        os.close(fd)
    if len(data) > _MAX_CLAUDE_MD:
        return _Unreadable(f"it is larger than {_MAX_CLAUDE_MD} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:  # trw-fail-silent-allow: the reason is returned and reported as a warning
        return _Unreadable(f"it is not UTF-8 ({exc})")


def _recorded_targets(project_root: Path) -> list[str]:
    """Return the clients this project RECORDED, or [] if it recorded none.

    Separate from :func:`_recorded_or_detected_targets` because some callers
    must be able to tell "nothing recorded" from "recorded, and it is this" —
    falling back to detection is right when answering a question, and wrong
    when deciding what to write back into the record.
    """
    import yaml

    config_path = project_root / ".trw" / "config.yaml"
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        recorded = data.get("target_platforms") or []
        if isinstance(recorded, list):
            return [str(entry) for entry in recorded]
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        logger.debug("claude_md_target_platforms_unreadable", project_root=str(project_root), exc_info=True)
    return []


# Project-scoped paths whose presence indicates a client is in use here. TOTAL over
# ``SUPPORTED_IDES`` (wiring-defect pattern P11). Install-time evidence only: the update path adopts a
# client through ``_client_adoption`` (file-by-file render/hash proof), never from a marker.
_CLIENT_EVIDENCE_MARKERS: dict[str, tuple[str, ...]] = {
    "claude-code": (".claude",),
    "cursor-ide": (".cursor",),
    "cursor-cli": (".cursor/cli.json",),
    "opencode": (".opencode", "opencode.json"),
    "codex": (".codex",),
    "copilot": (".github/agents",),
    "antigravity-cli": ("ANTIGRAVITY.md",),
    "grok": (".grok",),
}


def clients_with_markers_on_disk(project_root: Path) -> list[str]:
    """Clients whose on-disk marker is present, WITHOUT the scaffolding exclusion.

    At INSTALL time nothing has been
    scaffolded yet, so a ``.claude/`` or ``.cursor/`` already on disk really is
    the user's. What still must not count at install is the machine-global half
    of detection — ``shutil.which("cursor")`` and the ``CURSOR_*`` env vars —
    which is exactly what this table-driven check leaves out.
    """
    return [
        client_id
        for client_id, markers in _CLIENT_EVIDENCE_MARKERS.items()
        if any((project_root / marker).exists() for marker in markers)
    ]


def _recorded_or_detected_targets(project_root: Path) -> list[str]:
    """Prefer the clients the project RECORDED over what is on disk.

    Detection cannot answer this after an install. TRW writes ``.claude/``
    (agents, hooks, skills) and ``.cursor/`` into every project whatever the
    client, so from the first install onward a codex-only project reports
    claude-code too — and a decision keyed on detection flips back the moment
    the user re-runs the installer. ``target_platforms`` is what the user
    actually selected, so it is the durable answer; detection is the fallback
    for projects predating the record.
    """
    import yaml

    config_path = project_root / ".trw" / "config.yaml"
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        recorded = data.get("target_platforms") or []
        if isinstance(recorded, list) and recorded:
            return [str(entry) for entry in recorded]
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        logger.debug("claude_md_target_platforms_unreadable", project_root=str(project_root), exc_info=True)

    from ._utils import resolve_ide_targets

    return resolve_ide_targets(project_root)
