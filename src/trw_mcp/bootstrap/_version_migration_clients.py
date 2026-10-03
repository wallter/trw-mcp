"""Per-client stale bundled-artifact cleanup (FIX A — client-adapter parity).

Belongs to the ``_version_migration.py`` facade. Re-exported there for
back-compat with callers/tests.

Background
----------
The original stale-artifact cleanup (``_remove_stale_artifacts``) only covered
``.claude/{skills,agents,hooks}`` and ``.opencode/{commands,agents,skills}``.
The codex/cursor/copilot mirror directories were never swept, so when a
bundled skill or agent was dropped upstream its stale copy lingered in those
client dirs forever (e.g. a ``.cursor/skills/trw-delegate/`` left behind after
``trw-delegate`` is removed from the bundle).

Design
------
Every TRW-generated client artifact is ``trw-`` prefixed and its name is derived
from a bundled source (a data directory, a template dict, or a curated list).
Cleanup therefore only ever considers on-disk entries that:

1. live in a known TRW mirror directory,
2. carry the ``trw-`` prefix (so user files in shared dirs — ``.github/``,
   ``.cursor/``, ``.antigravitycli/`` — are never touched), **and**
3. match the artifact KIND (directory vs file) for that surface, **and**
4. are NOT in the CURRENT bundled-name source.

An entry meeting all four is a dropped TRW artifact. Anything
still bundled is left in place (the per-client installer refreshes it). This
keeps the "never remove files that aren't derived from bundled names" invariant
without needing to widen the managed-artifacts manifest schema. A whole entry is
removed only when the pre-run manifest proves TRW wrote it (PRD-INFRA-190-FR06);
otherwise it is kept and reported ``not_installer_owned``.

File granularity inside a KEPT skill directory
----------------------------------------------
The four rules above are *directory*-granular for skill surfaces, which left a
hole once the manifest recorders became bundle-driven (PRD-FIX-121). Drop ONE
file from a skill that is otherwise still bundled and the directory is kept, so
nothing sweeps the orphaned file — while the recorder, which enumerates from the
bundle, no longer records it. The next time upstream re-adds that filename with
new content, ``_is_user_modified`` finds no manifest record, no framework match,
and preserves it: the file is frozen at its old content forever, and reported as
``preserved`` as though the user had authored it. Measured 2026-07-28.

:func:`_remove_stale_files_in_kept_dir` closes that at file granularity. It
deletes a file only when the pre-run manifest proves **TRW wrote exactly these
bytes** and the bundle no longer contains the key. A file with no manifest
record (the user created it) or whose content has drifted from the record (the
user edited it) is preserved — CONSTITUTION HB-2 is why the sweep is keyed on
proof of authorship rather than on "it is inside a trw- directory".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

from ._ownership_proof import _trw_authored, remove_proven
from ._retire import as_retirement, record_retirement, retire_file

logger = structlog.get_logger(__name__)

# Every TRW-generated client artifact is trw-prefixed; the prefix gate is the
# guard that keeps user-authored files in shared client dirs safe.
_TRW_PREFIX = "trw-"


@dataclass(frozen=True, slots=True)
class ClientArtifactSurface:
    """One TRW-mirrored client directory subject to stale cleanup.

    Attributes:
        client_dir: Path relative to the target project root
            (e.g. ``".agents/skills"``, ``".cursor/agents"``).
        is_dir_artifact: ``True`` when artifacts are ``<name>/`` directories
            (skills), ``False`` when they are files (agents/commands).
        bundled_names: Callable returning the CURRENT set of bundled artifact
            names for this surface, including any suffix used on disk
            (``.md`` / ``.toml`` / ``.agent.md``). Names absent from this set
            but present on disk are stale.
        log_event: structlog event name emitted on a removal failure.
        bundled_files: For ``is_dir_artifact`` surfaces only — callable
            returning the CURRENT set of repo-relative *file* keys the surface
            bundles, in the same spelling the manifest recorder uses. Derived
            from the recorder's own content callable so the two cannot drift.
            ``None`` disables the file-granular sweep for this surface —
            correct for file surfaces, a silent hole for a directory one, so
            ``tests/test_bootstrap_manifest_ownership.py::
            test_every_directory_surface_declares_a_file_key_source``
            fails when a directory surface leaves it ``None``.
        exact_proof: Only the file's own repo-relative manifest key proves TRW wrote it. ``None`` means
            "exact for a file surface, the documented suffix rule for a skill-dir mirror"
            (CLIENT-SURFACE-SUFFIX-PROOF). ``.claude/agents`` passes ``False``: its keys are bare ``<agent>.md``.
        follows_canonical: A skill dir whose ``.claude/skills/<name>`` source is live is the project's own and
            stays (PRD-FIX-139-FR03). ``False`` only for ``.claude/skills`` itself.
    """

    client_dir: str
    is_dir_artifact: bool
    bundled_names: Callable[[], set[str]]
    log_event: str
    bundled_files: Callable[[], set[str]] | None = None
    exact_proof: bool | None = None
    follows_canonical: bool = True


def _codex_skill_names() -> set[str]:
    from ._client_skills import skill_names

    return set(skill_names("codex"))


def _bundled_agent_filenames(client: str) -> set[str]:
    """Filenames the bundled agent set produces for *client*.

    Derived from the bundle plus the client's registry entry, so the stale
    sweep tracks the bundle automatically. It used to read a per-client
    template dictionary, which is why retiring a stub name required a second
    edit — and why a name dropped from one dictionary lingered in installs.
    """
    from trw_mcp.agents.agent_formats import agent_format_for
    from trw_mcp.exceptions import AgentFormatError

    from ._utils import _DATA_DIR

    try:
        fmt = agent_format_for(client)
    except AgentFormatError:
        return set()
    source = _DATA_DIR / "agents"
    if not fmt.supports_agents or not source.is_dir():
        return set()
    return {f"{path.stem}{fmt.filename_suffix}" for path in source.glob("*.md")}


def _codex_agent_names() -> set[str]:
    return _bundled_agent_filenames("codex")


def _cursor_skill_names() -> set[str]:
    from ._cursor_ide import _IDE_CURATED_SKILLS

    return set(_IDE_CURATED_SKILLS)


def _cursor_agent_names() -> set[str]:
    return _bundled_agent_filenames("cursor-ide")


def _cursor_command_names() -> set[str]:
    from ._cursor_ide import _TRW_COMMANDS

    return {f"{name}.md" for name, _ in _TRW_COMMANDS}


def _copilot_skill_names() -> set[str]:
    # Copilot ships a CURATED subset, not the full canonical set -- sourcing the
    # whole set here once left ~14 stale copilot skills uncleaned
    # (release-verify 2026-07-17 P1). The subset is _client_skills' membership.
    from ._client_skills import skill_names

    return set(skill_names("copilot"))


def _copilot_agent_names() -> set[str]:
    return _bundled_agent_filenames("copilot")


def _antigravity_agent_names() -> set[str]:
    return _bundled_agent_filenames("antigravity-cli")


def _grok_agent_names() -> set[str]:
    return _bundled_agent_filenames("grok")


# File-key sources for the in-directory sweep. Each one delegates to the SAME
# ``contents()`` callable the surface's manifest recorder enumerates, so the
# sweep's idea of "still bundled" can never drift from the recorder's idea of
# "still ours" — the drift between those two is the defect this closes.
def _codex_skill_file_keys() -> set[str]:
    return set(codex_artifact_contents())


def _cursor_skill_file_keys() -> set[str]:
    from ._cursor_ide import cursor_ide_skill_contents

    return set(cursor_ide_skill_contents())


def _copilot_skill_file_keys() -> set[str]:
    from ._copilot import copilot_skill_contents

    return set(copilot_skill_contents())


# Per-client mirror surfaces. Bundled-name sources are looked up lazily so a
# missing/renamed symbol in a sibling module degrades to "no cleanup" for that
# surface rather than breaking the whole update.
_CLIENT_ARTIFACT_SURFACES: tuple[ClientArtifactSurface, ...] = (
    # Codex: skills are canonical skill directories rendered for codex; agents
    # are the bundled specialists rendered as .toml.
    ClientArtifactSurface(
        ".agents/skills",
        True,
        _codex_skill_names,
        "stale_codex_skill_removal_failed",
        bundled_files=_codex_skill_file_keys,
    ),
    ClientArtifactSurface(".codex/agents", False, _codex_agent_names, "stale_codex_agent_removal_failed"),
    # Cursor IDE: skills are DIRECTORIES from the curated list; agents/commands
    # are trw-*.md files.
    ClientArtifactSurface(
        ".cursor/skills",
        True,
        _cursor_skill_names,
        "stale_cursor_skill_removal_failed",
        bundled_files=_cursor_skill_file_keys,
    ),
    ClientArtifactSurface(".cursor/agents", False, _cursor_agent_names, "stale_cursor_agent_removal_failed"),
    ClientArtifactSurface(".cursor/commands", False, _cursor_command_names, "stale_cursor_command_removal_failed"),
    # Copilot: skills are DIRECTORIES from data/skills; agents are the bundled
    # specialists rendered as trw-*.agent.md files.
    ClientArtifactSurface(
        ".github/skills",
        True,
        _copilot_skill_names,
        "stale_copilot_skill_removal_failed",
        bundled_files=_copilot_skill_file_keys,
    ),
    ClientArtifactSurface(".github/agents", False, _copilot_agent_names, "stale_copilot_agent_removal_failed"),
    # Antigravity: `.agents/agents` is the directory its own subagent reference
    # documents (PRD-CORE-252 moved it off `.antigravitycli/agents`).
    ClientArtifactSurface(".agents/agents", False, _antigravity_agent_names, "stale_antigravity_agent_removal_failed"),
    # Grok: `.grok/agents` is recorded by the managed-artifact recorders, so a retired agent's copy is provable
    # and must be swept like every other client's (GROK-AGENTS-STALE-SWEEP).
    ClientArtifactSurface(".grok/agents", False, _grok_agent_names, "stale_grok_agent_removal_failed"),
)


def _remove_stale_files_in_kept_dir(
    surface: ClientArtifactSurface,
    entry: Path,
    bundled_keys: set[str],
    manifest_hashes: dict[str, str] | None,
    result: dict[str, list[str]],
    target_dir: Path,
) -> None:
    """Remove files TRW wrote into a KEPT skill dir that the bundle has since dropped.

    The directory-granular sweep keeps *entry* because the skill is still
    bundled, so without this pass a file dropped from inside it lingers forever
    with no manifest record — and freezes at its old bytes the moment upstream
    re-adds that filename (PRD-FIX-121 round-2 regression).

    Only a file the pre-run manifest records is TRW's to retire (no record means a file the user created inside
    a TRW skill dir, which is never touched). A recorded file is deleted in place when its bytes still hash to
    that record or git holds it clean; an uncommitted edit is kept and named with the command that removes it.
    A surface whose bundle contributes no key under this directory is skipped
    entirely: an unreadable/absent content source must not read as "everything
    here is stale".
    """
    prefix = f"{surface.client_dir}/{entry.name}/"
    if not any(key.startswith(prefix) for key in bundled_keys):
        return
    for path in sorted(entry.rglob("*")):
        if not path.is_file():
            continue
        key = f"{prefix}{path.relative_to(entry).as_posix()}"
        recorded = (manifest_hashes or {}).get(key)
        if key in bundled_keys or recorded is None:
            continue
        record_retirement(result, as_retirement(key, retire_file(path, target_dir, {recorded})))


def _surface_bundled_file_keys(surface: ClientArtifactSurface) -> set[str]:
    """Current bundled file keys for *surface*, or an empty set when undecidable."""
    if surface.bundled_files is None:
        return set()
    try:
        return surface.bundled_files()
    except Exception:  # justified: a broken content source must not abort cleanup
        logger.debug("client_bundled_files_failed", surface=surface.client_dir, exc_info=True)
        return set()


def _remove_stale_client_surface(
    surface: ClientArtifactSurface,
    target_dir: Path,
    result: dict[str, list[str]],
    *,
    manifest_hashes: dict[str, str] | None = None,
    shipped_skills: set[str] | None = None,
) -> None:
    """Remove stale trw-prefixed artifacts from a single client mirror surface.

    *shipped_skills* is every skill TRW ships (the full bundle); ``None`` reads it from the installed bundle.
    """
    from ._optional_skills import CONDITIONAL_SKILLS

    root = target_dir / surface.client_dir
    if not root.is_dir():
        return
    try:
        bundled = surface.bundled_names()
    except Exception:  # justified: a broken bundled-name source must not abort cleanup
        logger.debug("client_bundled_names_failed", surface=surface.client_dir, exc_info=True)
        return
    bundled_keys = _surface_bundled_file_keys(surface)

    for entry in sorted(root.iterdir()):
        name = entry.name
        # Guard 1: only TRW-derived (trw-prefixed) names are ever candidates.
        if not name.startswith(_TRW_PREFIX):
            continue
        # Guard 2: kind must match (never unlink a dir / rmtree a file).
        if surface.is_dir_artifact:
            if not entry.is_dir():
                continue
        elif not entry.is_file():
            continue
        # Guard 3: still bundled → keep the directory; the installer refreshes
        # it in place. Its CONTENTS still need a file-granular sweep, because a
        # single file dropped from a kept skill is invisible to every guard above.
        if name in bundled:
            if surface.is_dir_artifact and bundled_keys:
                _remove_stale_files_in_kept_dir(surface, entry, bundled_keys, manifest_hashes, result, target_dir)
            continue
        # A flag-gated skill is retire_disabled_skills' to remove, on every surface: a client's curated list
        # (or the flag being off) never makes it this sweep's (S8a lead ruling).
        if surface.is_dir_artifact and name in CONDITIONAL_SKILLS:
            continue
        # PRD-FIX-139-FR03: a mirror follows its source. A skill dir TRW does not ship at all but still present
        # under the canonical ``.claude/skills`` is the project's own (retired-name collision, or a local skill)
        # and its projections stay with it. A skill TRW still ships that this client's list dropped always has a
        # live canonical copy, so it is not exempt: it is stale for this client (codex S8a r3).
        if (
            surface.follows_canonical
            and surface.is_dir_artifact
            and (target_dir / ".claude" / "skills" / name).is_dir()
        ):
            if shipped_skills is None:
                from ._artifact_names import _get_bundled_names

                shipped_skills = set(_get_bundled_names()["skills"])
            if name not in shipped_skills:
                result.setdefault("preserved", []).append(f"preserved:{entry} (mirror of a live .claude/skills source)")
                continue
        if _is_channel_artifact(entry, manifest_hashes, target_dir):
            continue
        # CLIENT-SURFACE-SUFFIX-PROOF: a file surface (agents, commands) is recorded under its exact key, so only
        # that key proves it; a skill-dir mirror keeps the documented suffix rule (its bare .claude key holds the
        # same bytes).
        exact = (not surface.is_dir_artifact) if surface.exact_proof is None else surface.exact_proof
        remove_proven(entry, manifest_hashes, target_dir, result, exact=exact)


def _is_channel_artifact(entry: Path, manifest_hashes: dict[str, str] | None, target_dir: Path) -> bool:
    """A channel-rendered ``trw-*`` file: not in the bundle, still TRW's, and never this sweep's to retire.

    The Claude Code and OpenCode distill explorers are installed and withdrawn by their own channels (distill
    entitlement), so the retirement sweep never touches them. AG-EXPLORER-UNINSTALL-ORPHAN: the Antigravity explorer
    is skipped only while recorded with unchanged bytes; any other copy falls through to the not_installer_owned note.
    """
    from trw_mcp.channels.antigravity._explorer_subagent import EXPLORER_AGENT_RELPATH as AG_EXPLORER
    from trw_mcp.channels.claude_code._explorer_subagent import EXPLORER_AGENT_RELPATH as CC_EXPLORER
    from trw_mcp.channels.opencode._explorer_agent import EXPLORER_AGENT_RELPATH as OC_EXPLORER

    rel = entry.relative_to(target_dir).as_posix()
    if rel in (CC_EXPLORER, OC_EXPLORER):
        return True
    return rel == AG_EXPLORER and _trw_authored(entry, manifest_hashes or {}, target_dir, exact=True)


def codex_artifact_contents() -> dict[str, bytes]:
    """``{repo-relative path: bundled bytes}`` for every codex mirror artifact.

    The SAME source ``_codex.install_codex_skills`` writes from, so the recorder
    can never key on a path the installer does not produce, and the ownership
    decision compares against the same bytes the writer would have written. Read
    once per ``_write_manifest`` call.

    ``.codex/agents`` is deliberately absent since PRD-CORE-252: codex agents are
    materialized from the shared bundle like every other client's, and are
    recorded by ``_managed_client_artifacts.bundled_agent_contents``.
    """
    from ._client_skills import PRD_READY_CONTRACTS, skill_files, skill_names
    from ._codex import _CODEX_SKILLS_DIR

    contents: dict[str, bytes] = {}
    # A folded readiness phase has no directory of its own: trw-prd-ready's files include its contract.
    for name in sorted(set(skill_names("codex")) - set(PRD_READY_CONTRACTS["codex"])):
        try:
            for filename, data in skill_files("codex", name):
                contents[f"{_CODEX_SKILLS_DIR}/{name}/{filename}"] = data
        except OSError:
            logger.warning("codex_bundled_read_failed", skill=name)
    return contents


def _codex_manifest_hashes(target_dir: Path, prev_hashes: dict[str, str] | None = None) -> dict[str, str]:
    """SHA256 of installed codex agent/skill files, keyed by repo-relative path.

    Persisted into ``managed-artifacts.yaml`` (``content_hashes``) by
    ``_write_manifest`` so the NEXT update can distinguish a user-edited codex
    artifact from a stale-but-unmodified one, enabling content-aware refresh
    (FIX B). Keys MUST match the ``rel_path`` used by
    ``_codex.install_codex_skills`` so the modification guard lines up:
      - ``.agents/skills/<skill>/<file>``

    ``.codex/agents/<name>.toml`` left this recorder with PRD-CORE-252-FR04:
    codex agents are materializations of the shared bundle now, recorded by
    ``_managed_client_artifacts.bundled_agent_contents`` against the bytes the
    shared installer actually writes.

    PRD-FIX-121-FR01: *prev_hashes* is the manifest as it stood BEFORE this run.
    An artifact diverging from both the current bundle and its own previous
    record is a user edit and is **omitted** — recording it would launder the
    user's bytes into TRW's ownership baseline and destroy them on the next run.
    Enumeration is bundle-driven (not a filesystem scan) so the recorder cannot
    claim a path the installer never writes.
    """
    import hashlib

    from ._managed_client_artifacts import artifact_user_edited

    hashes: dict[str, str] = {}
    for key, incoming in codex_artifact_contents().items():
        dest = target_dir / key
        if not dest.is_file():
            continue
        if artifact_user_edited(dest, key, incoming, prev_hashes):
            logger.info("codex_manifest_ownership_declined", path=key)
            continue
        try:
            hashes[key] = hashlib.sha256(dest.read_bytes()).hexdigest()
        except OSError:
            logger.warning("codex_content_hash_failed", path=str(dest))
    return hashes


def client_skill_lists() -> dict[str, set[str] | None]:
    """Each client skill mirror's own shipped list, as the stale sweep judges it (DOCTOR-PER-CLIENT-SKILL-PREDICATE).

    ``None`` means the list is unknown (a failing source, or no OpenCode inventory): the sweep judges nothing
    there, and neither may the doctor. Built from the sweep's own name sources so the two cannot disagree.
    """
    from ._artifact_names import _opencode_skill_names
    from ._utils import _DATA_DIR

    lists: dict[str, set[str] | None] = {}
    for surface in _CLIENT_ARTIFACT_SURFACES:
        if surface.is_dir_artifact:
            try:
                lists[surface.client_dir] = set(surface.bundled_names())
            except Exception:  # justified: a broken bundled-name source means an unknown list, as in the sweep
                logger.debug("client_bundled_names_failed", surface=surface.client_dir, exc_info=True)
                lists[surface.client_dir] = None
    opencode = _opencode_skill_names(_DATA_DIR / "opencode", _DATA_DIR / "skills")
    lists[".opencode/skills"] = None if opencode is None else set(opencode)
    return lists


def _remove_stale_client_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    *,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Sweep every codex/cursor/copilot mirror surface for dropped artifacts.

    Runs unconditionally (each surface self-skips when its dir is absent), so a
    project that installed a client and later drops a bundled skill/agent gets
    the stale copy removed on the next update — matching the existing
    ``.claude``/``.opencode`` cleanup contract.

    *manifest_hashes* is the manifest as it stood BEFORE this run (threaded from
    ``update_project``). It is the only evidence that a file inside a kept skill
    directory was TRW's own write, so omitting it disables the file-granular
    sweep rather than guessing — the safe direction (a stale file survives; no
    user file is deleted).
    """
    for surface in _CLIENT_ARTIFACT_SURFACES:
        _remove_stale_client_surface(surface, target_dir, result, manifest_hashes=manifest_hashes)
