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
   ``.cursor/``, ``.gemini/`` — are never touched), **and**
3. match the artifact KIND (directory vs file) for that surface, **and**
4. are NOT in the CURRENT bundled-name source.

An entry meeting all four is a dropped TRW artifact and is removed. Anything
still bundled is left in place (the per-client installer refreshes it). This
keeps the "never remove files that aren't derived from bundled names" invariant
without needing to widen the managed-artifacts manifest schema. ``dry_run``
reports ``would remove:<path>`` and deletes nothing (parity with
``_remove_stale_set``).
"""

from __future__ import annotations

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
    """

    client_dir: str
    is_dir_artifact: bool
    bundled_names: Callable[[], set[str]]
    log_event: str


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


# Per-client mirror surfaces. Bundled-name sources are looked up lazily so a
# missing/renamed symbol in a sibling module degrades to "no cleanup" for that
# surface rather than breaking the whole update.
_CLIENT_ARTIFACT_SURFACES: tuple[ClientArtifactSurface, ...] = (
    # Codex: skills mirror bundled DIRECTORIES under data/codex/skills; agents
    # are the 4 in-repo .toml templates.
    ClientArtifactSurface(".agents/skills", True, _codex_skill_names, "stale_codex_skill_removal_failed"),
    ClientArtifactSurface(".codex/agents", False, _codex_agent_names, "stale_codex_agent_removal_failed"),
    # Cursor IDE: skills are DIRECTORIES from the curated list; agents/commands
    # are trw-*.md files.
    ClientArtifactSurface(".cursor/skills", True, _cursor_skill_names, "stale_cursor_skill_removal_failed"),
    ClientArtifactSurface(".cursor/agents", False, _cursor_agent_names, "stale_cursor_agent_removal_failed"),
    ClientArtifactSurface(".cursor/commands", False, _cursor_command_names, "stale_cursor_command_removal_failed"),
    # Copilot: skills are DIRECTORIES from data/skills; agents are flattened
    # trw-*.agent.md files.
    ClientArtifactSurface(".github/skills", True, _copilot_skill_names, "stale_copilot_skill_removal_failed"),
    ClientArtifactSurface(".github/agents", False, _copilot_agent_names, "stale_copilot_agent_removal_failed"),
)


def _remove_stale_client_surface(
    surface: ClientArtifactSurface,
    target_dir: Path,
    result: dict[str, list[str]],
    *,
    dry_run: bool,
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
        # Guard 3: still bundled → keep; the installer refreshes it in place.
        if name in bundled:
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


def _codex_manifest_hashes(target_dir: Path) -> dict[str, str]:
    """SHA256 of installed codex agent/skill files, keyed by repo-relative path.

    Persisted into ``managed-artifacts.yaml`` (``content_hashes``) by
    ``_write_manifest`` so the NEXT update can distinguish a user-edited codex
    artifact from a stale-but-unmodified one, enabling content-aware refresh
    (FIX B). Keys MUST match the ``rel``/``rel_path`` used by
    ``_codex.generate_codex_agents`` / ``install_codex_skills`` so the
    modification guard lines up:
      - ``.codex/agents/<name>.toml``
      - ``.agents/skills/<skill>/<file>``
    """
    import hashlib

    hashes: dict[str, str] = {}

    def _record(path: Path, key: str) -> None:
        try:
            if path.is_file():
                hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            logger.warning("codex_content_hash_failed", path=str(path))

    agents_dir = target_dir / ".codex" / "agents"
    if agents_dir.is_dir():
        for agent in sorted(agents_dir.iterdir()):
            if agent.is_file() and agent.suffix == ".toml" and agent.name.startswith(_TRW_PREFIX):
                _record(agent, f".codex/agents/{agent.name}")

    skills_root = target_dir / ".agents" / "skills"
    if skills_root.is_dir():
        for skill in sorted(skills_root.iterdir()):
            if not (skill.is_dir() and skill.name.startswith(_TRW_PREFIX)):
                continue
            for skill_file in sorted(skill.iterdir()):
                if skill_file.is_file():
                    _record(skill_file, f".agents/skills/{skill.name}/{skill_file.name}")

    return hashes


def _remove_stale_client_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    dry_run: bool = False,
) -> None:
    """Sweep every codex/cursor/copilot mirror surface for dropped artifacts.

    Runs unconditionally (each surface self-skips when its dir is absent), so a
    project that installed a client and later drops a bundled skill/agent gets
    the stale copy removed on the next update — matching the existing
    ``.claude``/``.opencode`` cleanup contract.
    """
    for surface in _CLIENT_ARTIFACT_SURFACES:
        _remove_stale_client_surface(surface, target_dir, result, dry_run=dry_run)
