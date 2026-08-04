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

from ._file_ops import find_marker_line_span

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


def _claude_md_import_syntax(project_root: Path, ide_targets: list[str] | None = None) -> str:
    """Return the in-file import syntax safe for every CLAUDE.md reader here.

    Returns ``"at_path"`` only when *all* clients that read this project's
    CLAUDE.md declare that syntax; ``"none"`` otherwise, which keeps the block
    inline. A single incapable reader must veto externalization — an import it
    cannot resolve leaves it with an instruction file that carries nothing, and
    that failure is invisible because the file still exists and still parses.

    *ide_targets* is the authoritative answer and callers SHOULD pass it: it is
    resolved (``resolve_ide_targets``) before installation writes anything.
    Falling back to ``detect_ide`` here is a last resort and is deliberately
    unreliable in one direction — installation itself creates ``.claude/`` *and*
    ``.cursor/`` artifacts, so post-install detection reports both clients for
    every project and can no longer identify the target. Vetoing on that
    ambiguity is safe (inline always works); trusting it would not be.

    Detection failures veto too: unknown capability is not capability.
    """
    from trw_mcp.models.config._profiles import resolve_client_profile

    if ide_targets is None:
        try:
            from trw_mcp.bootstrap._utils import detect_ide

            ide_targets = detect_ide(project_root)
        except Exception:  # justified: fail-safe — undetectable clients must not enable externalization
            logger.warning("claude_md_client_detection_failed", project_root=str(project_root), exc_info=True)
            return "none"

    # No target means the default claude-code scaffold (see the write decision
    # in state/claude_md/_agents_md).
    readers = ide_targets or ["claude-code"]
    syntaxes: set[str] = set()
    for client_id in readers:
        try:
            profile = resolve_client_profile(client_id)
        except Exception:  # justified: fail-safe — an unresolvable profile is an unknown reader
            return "none"
        if profile.write_targets.claude_md:
            syntaxes.add(profile.instruction_import_syntax)

    # An EMPTY reader set means no target declares CLAUDE.md, yet one is still
    # written — `_determine_write_target_decision`'s fallback for a cursor-ide-only
    # project, which has nowhere else to put the protocol. That client declares
    # `instruction_import_syntax="none"`, so an import there would leave it with an
    # instruction file it cannot resolve. Inline is the only safe carrier.
    #
    # Deliberately NOT vetoing merely because cursor-ide appears alongside others:
    # `detect_ide` sets cursor-ide from `shutil.which("cursor")`, a machine-global
    # signal, so on any box with Cursor installed that would disable externalization
    # for every project — turning a narrow correctness fix into a blanket regression.
    # When claude-code is also a target it is the CLAUDE.md reader and resolves the
    # import; cursor-ide is not a declared consumer of that file.
    if not syntaxes:
        return "none"
    if syntaxes == {"at_path"}:
        return "at_path"
    return "none"


def claude_md_is_claimed(project_root: Path, ide_targets: list[str] | None = None) -> bool:
    """Return whether any install target here actually reads CLAUDE.md.

    One definition for all three bootstrap writers. They previously each wrote
    the TRW block unconditionally, so the block came back on the next
    ``update-project`` even where no client reads the file — the same
    fixed-in-one-place-copied-in-three shape that let the carrier be reverted.

    *ide_targets* is authoritative when the caller already resolved it;
    otherwise it is resolved here rather than detected, because post-install
    detection cannot distinguish TRW's own ``.claude/`` from the user's.
    """
    from trw_mcp.state.claude_md._orphan_strip import _any_client_writes_claude_md

    if ide_targets is not None:
        # An explicitly passed list is the caller's install selection — as
        # authoritative as the record, and never raw detection.
        return _any_client_writes_claude_md(ide_targets, from_record=True)

    recorded = _recorded_targets(project_root)
    if recorded:
        return _any_client_writes_claude_md(recorded, from_record=True)
    return _any_client_writes_claude_md(_recorded_or_detected_targets(project_root))


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


# Project-scoped paths whose presence would indicate a client is in use here.
# TOTAL over ``SUPPORTED_IDES`` — every installable client declares a candidate
# marker, including the ones that turn out to be unusable. That is the point:
# which candidates survive is DERIVED (:func:`_trw_scaffolds_marker`), not
# hand-listed, so the exclusion cannot silently fall out of date the way a
# module-local subset does (wiring-defect pattern P11).
_CLIENT_EVIDENCE_MARKERS: dict[str, tuple[str, ...]] = {
    "claude-code": (".claude",),
    "cursor-ide": (".cursor",),
    "cursor-cli": (".cursor/cli.json",),
    "opencode": (".opencode", "opencode.json"),
    "codex": (".codex",),
    "copilot": (".github/agents",),
    "antigravity-cli": ("ANTIGRAVITY.md",),
}


def _paths_overlap(one: str, other: str) -> bool:
    """True when the two repo-relative paths are the same file or nest."""
    return one == other or one.startswith(f"{other}/") or other.startswith(f"{one}/")


def _trw_scaffolds_marker(client_id: str, marker: str) -> bool:
    """True when TRW itself creates *marker* without the user choosing *client_id*.

    Two ways that happens, and the uninstall registry — TRW's own record of what
    it installs — answers both:

    * the marker is (or contains) a **core** surface, written into every project
      whatever the client. ``.claude/`` is the case that matters: hooks, skills
      and agents are universal artifacts, so ``.claude/`` is our output in a
      codex-only project too.
    * the marker is (or contains) a surface TRW installs for a **different**
      client. ``.cursor/`` is that case: a cursor-cli install writes
      ``.cursor/cli.json`` and ``.cursor/hooks/`` under it, so the directory says
      "some cursor surface was installed", never "this developer uses the IDE".

    Deriving it is what makes it durable. The predecessor hand-listed a single
    exclusion (claude-code) with the right reasoning in its docstring and no
    mechanism behind it, so ``.cursor/`` — created by TRW on any machine with the
    ``cursor`` binary on PATH — voted cursor-ide into an append-only record it
    can never leave. A new client that starts writing under ``.codex/`` now
    retires codex's marker automatically instead of repeating that.
    """
    from trw_mcp.client_profiles.catalog import client_scaffold_relpaths, core_scaffold_relpaths

    from ._utils import SUPPORTED_IDES

    scaffolded: set[str] = set(core_scaffold_relpaths())
    for other in SUPPORTED_IDES:
        if other != client_id:
            scaffolded |= client_scaffold_relpaths(other)
    return any(_paths_overlap(marker, path) for path in scaffolded)


def _file_evidenced_clients(project_root: Path) -> list[str]:
    """Clients with project-scoped evidence TRW did not fabricate.

    ``detect_ide`` deliberately also fires on machine-global signals — ``which
    cursor``, ``which cursor-agent``, ``CURSOR_*`` env — and TRW then scaffolds
    whatever it detected. Those signals are right for a first install (they
    answer "what could this developer use?") and wrong for maintaining a
    project's recorded client list (which answers "what does THIS project
    use?"), because our own output becomes the next run's input.

    So a marker only counts when TRW could not have written it while installing
    something else. claude-code and cursor-ide currently have no such marker and
    are therefore never adopted from disk; adopting either later is an explicit
    ``update-project --ide <client>``.

    **Read the summary line as narrower than it sounds.** "TRW did not fabricate"
    is true of *installing something else*, which is what the predicate tests. It
    is NOT true of installing the client the marker evidences: measured against the
    catalog, 7 of the 8 markers in the table are paths TRW writes for that same
    client — ``.github/agents`` for copilot, ``.codex`` for codex, and so on. The
    predicate has no arm for that case.

    That gap is **latent, not live**: reaching it needs TRW to write client X's
    marker in a project where X is not recorded, and ``_run_post_update_phases``
    now derives its write targets from ``recorded or ide_targets`` rather than from
    detection, which closes it. Reproduced at HEAD — ``init-project --ide codex``
    plus three bare updates leaves the record ``['codex']``.

    It is not closed here because closing it honestly would reject every marker in
    the table and make this function permanently empty, which is a decision about
    whether the mechanism should exist at all. The measurement is pinned in
    ``tests/test_client_evidence_self_scaffolding.py`` so it cannot drift quietly.
    """
    found: list[str] = []
    for client_id, markers in _CLIENT_EVIDENCE_MARKERS.items():
        usable = [m for m in markers if not _trw_scaffolds_marker(client_id, m)]
        if any((project_root / marker).exists() for marker in usable):
            found.append(client_id)
    return found


def clients_with_markers_on_disk(project_root: Path) -> list[str]:
    """Clients whose on-disk marker is present, WITHOUT the scaffolding exclusion.

    The sibling :func:`_file_evidenced_clients` drops markers TRW writes itself,
    because on the update path our own output would otherwise be read back as
    the user's choice. At INSTALL time that exclusion is wrong: nothing has been
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


def _recorded_plus_newly_adopted(project_root: Path) -> list[str]:
    """The recorded clients, plus any newly adopted one with real evidence.

    This is what a bare ``update-project`` should write back: it still picks up
    a client the user genuinely added (an ``opencode.json`` appears), without
    letting our own scaffolding vote itself into the record.
    """
    recorded = _recorded_targets(project_root)
    if not recorded:
        return []
    return recorded + [c for c in _file_evidenced_clients(project_root) if c not in recorded]


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


def _apply_claude_md_carrier(
    claude_md_path: Path,
    result: dict[str, list[str]],
    project_root: Path,
    ide_targets: list[str] | None = None,
) -> bool:
    """Route the bootstrap CLAUDE.md write through the PRD-CORE-203 carrier.

    Returns ``True`` when the carrier handled the write (nothing further to do).

    Why this exists: this module used to hold a private inline-only replace,
    which made it a *second* appender that disagreed with the carrier-aware MCP
    sync path. Whichever ran last won. Because bootstrap runs on every
    ``init-project``/``update-project``, the inline writer routinely reverted an
    externalized CLAUDE.md and left an orphaned ``.trw/INSTRUCTIONS.md`` sidecar
    behind — a success-shaped failure, since both paths reported success. Both
    appenders now resolve the same carrier, so there is no ordering to lose.

    ``pointer_skip_guard`` alone could not prevent this: it only fires when the
    *whole file* classifies as POINTER, and a real CLAUDE.md full of user prose
    classifies as CONTENT. ``apply_carrier`` subsumes that guard — it classifies,
    heals a pointer, externalizes when the profile supports an in-file import,
    and otherwise falls back to the same inline merge as before.

    Import capability is resolved from **every client that will read this file**,
    not from a literal. ``CLAUDE.md`` is not exclusively Claude Code's: the write
    decision also emits it for a cursor-ide-only project
    (``_agents_md._determine_write_target_decision``), and cursor-ide declares
    ``instruction_import_syntax="none"``. Writing an ``@``-import there would
    produce an instruction file whose content that client cannot resolve — a file
    that reports success and says nothing, which is the precise failure this whole
    change exists to remove. So externalization requires that *all* CLAUDE.md
    readers support ``at_path``; otherwise the block stays inline.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state.claude_md._instruction_carrier import CarrierMode, apply_carrier

    config = get_config()
    if config.instruction_externalize == "off":
        return False

    import_syntax = _claude_md_import_syntax(project_root, ide_targets)

    # A half-written marker region (start present, end missing) is a real fault
    # the caller reports as "malformed". The carrier's merge would instead treat
    # the file as having no section and silently append a SECOND block, leaving
    # the damage in place and unreported. Decline, so the inline path below
    # surfaces it.
    if claude_md_path.exists():
        try:
            lines = claude_md_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        has_start = any(ln.strip() == _TRW_START_MARKER for ln in lines)
        has_end = any(ln.strip() == _TRW_END_MARKER for ln in lines)
        if has_start != has_end:
            return False

    rendered = _minimal_claude_md_trw_block()
    if not rendered:
        return False

    try:
        outcome = apply_carrier(
            claude_md_path,
            rendered,
            config.claude_md_max_lines,
            import_syntax=import_syntax,
            externalize=config.instruction_externalize,
            scope="root",
            external_filename=config.instruction_external_filename,
            project_root=project_root,
        )
    except Exception:  # justified: fail-open — bootstrap must never break on carrier failure
        logger.warning("bootstrap_claude_md_carrier_failed", target=str(claude_md_path), exc_info=True)
        return False

    if outcome.mode is CarrierMode.POINTER_SKIP:
        result.setdefault("preserved", []).append(str(claude_md_path))
    else:
        result.setdefault("updated", []).append(str(claude_md_path))
    return True


def _update_claude_md_trw_section(
    claude_md_path: Path,
    result: dict[str, list[str]],
    project_root: Path | None = None,
    ide_targets: list[str] | None = None,
) -> None:
    """Replace the auto-generated TRW section in CLAUDE.md.

    Preserves all user-written content above and below the markers. Routes
    through the PRD-CORE-203 carrier first (see :func:`_apply_claude_md_carrier`);
    the inline path below is the fallback when externalization is off or fails.

    *project_root* defaults to the file's own directory, which is correct for the
    repo-root CLAUDE.md this function is always called with.
    """
    if _apply_claude_md_carrier(claude_md_path, result, project_root or claude_md_path.parent, ide_targets):
        return

    # PRD-CORE-203 FR04: never clobber a single-source pointer file. The shared
    # guard (same helper used by ``_parser.merge_trw_section``) heals any stale
    # appended block and signals skip so the bootstrap update path leaves a
    # ``@AGENTS.md``-style pointer untouched.
    if claude_md_path.exists():
        from trw_mcp.state.claude_md._instruction_carrier import pointer_skip_guard

        if pointer_skip_guard(claude_md_path) is not None:
            result.setdefault("preserved", []).append(str(claude_md_path))
            return

    try:
        content = claude_md_path.read_text(encoding="utf-8")
    except OSError as exc:
        # TOCTOU-safe: the file may vanish between the guard and the read.
        result.setdefault("errors", []).append(f"Failed to read {claude_md_path}: {exc}")
        return
    new_block = _minimal_claude_md_trw_block()

    # Line-anchored whole-line matching only — an inline prose mention of a
    # marker (e.g. inside backticks) must never be mistaken for the section
    # boundary (repo rule "Marker / Sentinel Matching"; the substring form once
    # destroyed 705 ROADMAP lines).
    start_span = find_marker_line_span(content, _TRW_START_MARKER, anchor="start")
    end_span = find_marker_line_span(content, _TRW_END_MARKER, anchor="end")

    if start_span is not None and end_span is not None:
        # Replace the existing auto-generated section
        end_idx = end_span[1]
        # Also capture the header marker line if present on its own line above
        # the start marker (rfind semantics → last occurrence before start).
        header_span = find_marker_line_span(content[: start_span[0]], _TRW_HEADER_MARKER, anchor="start", last=True)
        replace_start = header_span[0] if header_span is not None else start_span[0]
        updated = content[:replace_start] + new_block + content[end_idx:]
        try:
            claude_md_path.write_text(updated, encoding="utf-8")
            result["updated"].append(str(claude_md_path))
        except OSError as exc:
            result["errors"].append(f"Failed to update {claude_md_path}: {exc}")
    elif start_span is None:
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
