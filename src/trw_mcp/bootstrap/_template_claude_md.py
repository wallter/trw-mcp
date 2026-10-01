"""Client-record helpers and the claude-code AGENTS.md carrier for bootstrap.

TRW 8.0 no longer writes ``CLAUDE.md``: Claude Code reads ``AGENTS.md``
natively, so claude-code shares the one ``AGENTS.md`` carrier the other
clients use. This module writes that block at install/update, retires a
TRW-only legacy ``CLAUDE.md``, and holds the recorded-client helpers the
bootstrap writers share.
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "_TRW_END_MARKER",
    "_TRW_HEADER_MARKER",
    "_TRW_START_MARKER",
    "claude_code_is_claimed",
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
    for key in ("created", "updated", "errors"):
        for entry in written.get(key, []):
            if entry not in result.setdefault(key, []):
                result[key].append(entry)


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
