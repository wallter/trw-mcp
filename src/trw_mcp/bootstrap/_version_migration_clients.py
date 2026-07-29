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

An entry meeting all four is a dropped TRW artifact and is removed. Anything
still bundled is left in place (the per-client installer refreshes it). This
keeps the "never remove files that aren't derived from bundled names" invariant
without needing to widen the managed-artifacts manifest schema. ``dry_run``
reports ``would remove:<path>`` and deletes nothing (parity with
``_remove_stale_set``).

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

import hashlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

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
    """

    client_dir: str
    is_dir_artifact: bool
    bundled_names: Callable[[], set[str]]
    log_event: str
    bundled_files: Callable[[], set[str]] | None = None


def _codex_skill_names() -> set[str]:
    from ._codex import _codex_skills_source_dir

    source = _codex_skills_source_dir()
    if not source.is_dir():
        return set()
    return {d.name for d in source.iterdir() if d.is_dir()}


def _codex_agent_names() -> set[str]:
    from ._codex import _CODEX_AGENT_TEMPLATES

    return set(_CODEX_AGENT_TEMPLATES)


def _cursor_skill_names() -> set[str]:
    from ._cursor_ide import _IDE_CURATED_SKILLS

    return set(_IDE_CURATED_SKILLS)


def _cursor_agent_names() -> set[str]:
    from ._cursor_ide import _TRW_SUBAGENTS

    return {f"{name}.md" for name, _ in _TRW_SUBAGENTS}


def _cursor_command_names() -> set[str]:
    from ._cursor_ide import _TRW_COMMANDS

    return {f"{name}.md" for name, _ in _TRW_COMMANDS}


def _copilot_skill_names() -> set[str]:
    # Copilot ships a CURATED subset (data/copilot/skills), not the full generic
    # data/skills set — sourcing the 28-name generic set here left ~14 stale
    # copilot skills uncleaned. Mirror _codex_skill_names and read the actual
    # per-client source dir. (release-verify 2026-07-17 P1)
    from ._copilot import _copilot_skills_source_dir

    source = _copilot_skills_source_dir()
    if not source.is_dir():
        return set()
    return {d.name for d in source.iterdir() if d.is_dir()}


def _copilot_agent_names() -> set[str]:
    from ._copilot import _COPILOT_AGENT_TEMPLATES

    return set(_COPILOT_AGENT_TEMPLATES)


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
    # Codex: skills mirror bundled DIRECTORIES under data/codex/skills; agents
    # are the 4 in-repo .toml templates.
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
    # Copilot: skills are DIRECTORIES from data/skills; agents are flattened
    # trw-*.agent.md files.
    ClientArtifactSurface(
        ".github/skills",
        True,
        _copilot_skill_names,
        "stale_copilot_skill_removal_failed",
        bundled_files=_copilot_skill_file_keys,
    ),
    ClientArtifactSurface(".github/agents", False, _copilot_agent_names, "stale_copilot_agent_removal_failed"),
)


def _remove_stale_files_in_kept_dir(
    surface: ClientArtifactSurface,
    entry: Path,
    bundled_keys: set[str],
    manifest_hashes: dict[str, str] | None,
    result: dict[str, list[str]],
    *,
    dry_run: bool,
) -> None:
    """Remove files TRW wrote into a KEPT skill dir that the bundle has since dropped.

    The directory-granular sweep keeps *entry* because the skill is still
    bundled, so without this pass a file dropped from inside it lingers forever
    with no manifest record — and freezes at its old bytes the moment upstream
    re-adds that filename (PRD-FIX-121 round-2 regression).

    Deletion requires POSITIVE proof of TRW authorship: the pre-run manifest
    records the key AND the on-disk bytes still hash to that record. Anything
    else is preserved:

    - no record at all → a file the user created inside a TRW skill dir;
    - record present but content drifted → TRW wrote it, the user edited it since.

    Both are uncommitted user work, and HB-2 forbids trading them for cleanliness.
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
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != recorded:
                continue
        except OSError:
            logger.debug(surface.log_event, path=str(path), exc_info=True)
            continue
        if dry_run:
            result.setdefault("updated", []).append(f"would remove:{path}")
            continue
        try:
            path.unlink()
            result.setdefault("updated", []).append(f"removed:{path}")
        except OSError:
            logger.debug(surface.log_event, path=str(path), exc_info=True)


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
    dry_run: bool,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Remove stale trw-prefixed artifacts from a single client mirror surface."""
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
                _remove_stale_files_in_kept_dir(surface, entry, bundled_keys, manifest_hashes, result, dry_run=dry_run)
            continue
        if dry_run:
            result.setdefault("updated", []).append(f"would remove:{entry}")
            continue
        try:
            if surface.is_dir_artifact:
                shutil.rmtree(entry)
            else:
                entry.unlink()
            result.setdefault("updated", []).append(f"removed:{entry}")
        except OSError:
            logger.debug(surface.log_event, path=str(entry), exc_info=True)


def codex_artifact_contents() -> dict[str, bytes]:
    """``{repo-relative path: bundled bytes}`` for every codex mirror artifact.

    The SAME sources ``_codex.generate_codex_agents`` / ``install_codex_skills``
    write from, so the recorder can never key on a path the installer does not
    produce, and the ownership decision compares against the same bytes the
    writer would have written. Read once per ``_write_manifest`` call.
    """
    from ._codex import _CODEX_AGENT_TEMPLATES, _CODEX_AGENTS_DIR, _CODEX_SKILLS_DIR, _codex_skills_source_dir

    contents: dict[str, bytes] = {
        f"{_CODEX_AGENTS_DIR}/{filename}": body.encode("utf-8") for filename, body in _CODEX_AGENT_TEMPLATES.items()
    }
    source = _codex_skills_source_dir()
    if source.is_dir():
        for skill in sorted(source.iterdir()):
            if not skill.is_dir():
                continue
            for skill_file in sorted(skill.iterdir()):
                if not skill_file.is_file():
                    continue
                try:
                    contents[f"{_CODEX_SKILLS_DIR}/{skill.name}/{skill_file.name}"] = skill_file.read_bytes()
                except OSError:
                    logger.warning("codex_bundled_read_failed", path=str(skill_file))
    return contents


def _codex_manifest_hashes(target_dir: Path, prev_hashes: dict[str, str] | None = None) -> dict[str, str]:
    """SHA256 of installed codex agent/skill files, keyed by repo-relative path.

    Persisted into ``managed-artifacts.yaml`` (``content_hashes``) by
    ``_write_manifest`` so the NEXT update can distinguish a user-edited codex
    artifact from a stale-but-unmodified one, enabling content-aware refresh
    (FIX B). Keys MUST match the ``rel``/``rel_path`` used by
    ``_codex.generate_codex_agents`` / ``install_codex_skills`` so the
    modification guard lines up:
      - ``.codex/agents/<name>.toml``
      - ``.agents/skills/<skill>/<file>``

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


def _remove_stale_client_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    dry_run: bool = False,
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
        _remove_stale_client_surface(surface, target_dir, result, dry_run=dry_run, manifest_hashes=manifest_hashes)
