"""Content-aware user-edit guard for per-client mirrored artifacts.

Belongs to the ``bootstrap`` package. Consumed by the copilot / cursor /
antigravity-cli generators and by ``_version_migration._write_manifest``.

Why this module exists
----------------------
``update-project`` refreshes every client's mirrored artifacts. Three of the
seven supported profiles used to do that with an unconditional write:

- ``.github/skills/**``          — ``shutil.copy2`` on both branches of an
  ``if existed and not force`` that only picked a result bucket;
- ``.cursor/skills/**``          — ``shutil.copytree(..., dirs_exist_ok=True)``;
- ``.cursor/agents``/``commands`` — documented as "always written".

so a user's hand edit was destroyed on every update (CONSTITUTION HB-2). The
mirror-image bug lived in the other three generators
(the per-client agent template writers and
``generate_copilot_path_instructions``): they short-circuited on
``existed and not force``, which preserves user edits but *also* freezes
TRW-owned files at their first-installed content forever.

Both directions are decided by the same question — **is this file TRW's own
last write, or the user's?** — so both are answered here, once.

The single predicate
--------------------
:func:`artifact_user_edited` delegates to
``_version_manifest._is_user_modified`` (the guard the ``.claude``/``.codex``/
``.opencode`` paths already use) with the *incoming bundled bytes* as the
framework baseline. A fifth private variant of this check is how the gap
appeared in the first place; there must not be a sixth.

The content registry
--------------------
:data:`MANAGED_CLIENT_ARTIFACT_SOURCES` maps each surface to a callable
returning ``{repo-relative path: bundled bytes}``. The *generators consume the
same callables they register here*, so the manifest sweep can never key on a
path the generator does not write (P11 "subset registry with no derivation").

The manifest sweep
------------------
:func:`managed_client_manifest_hashes` records a content hash **only for
artifacts that are not user-edited**. That is what makes the guard durable: a
preserved user edit gets no manifest entry, so it is preserved again on the
next update instead of being laundered into TRW's baseline and overwritten on
the second run.

That paragraph used to be an accurate description of THIS function and a false
description of the subsystem — its two sibling recorders recorded
unconditionally, so seven surfaces were preserved on run 1 and destroyed on
run 2 (PRD-FIX-121, wiring-defect class P10). It is now a subsystem property,
enforced by the registry in ``_manifest_recorders.py``: every recorder that
contributes keys to ``content_hashes`` is a registry member, and every member
routes its ownership decision through :func:`artifact_user_edited` /
:func:`artifact_user_edited_against`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def artifact_user_edited_against(
    dest: Path,
    key: str,
    framework_hashes: set[str],
    manifest_hashes: dict[str, str] | None,
) -> bool:
    """Return True when *dest* is a user edit, given an explicit framework baseline.

    The general form of :func:`artifact_user_edited`, for artifacts that have
    MORE than one legitimate framework rendering. ``.claude/agents/*.md`` is the
    live case: the installer resolves the capability-tier ``model:`` line before
    writing, so both the raw bundled bytes and the resolved bytes are "TRW wrote
    this" (see ``_version_manifest._framework_agent_hashes``). Collapsing that to
    a single hash would misclassify every healthy agent as a user edit.

    An EMPTY *framework_hashes* means the bundled baseline could not be read.
    Callers must treat that as "cannot decide" and fail toward preservation
    themselves — this function cannot, because ``_is_user_modified`` deliberately
    keeps its legacy "no baseline at all → updatable" fallback for the write side.
    """
    from ._version_manifest import _is_user_modified

    return _is_user_modified(dest, key, manifest_hashes, framework_hashes=framework_hashes)


def artifact_user_edited(
    dest: Path,
    key: str,
    incoming: bytes,
    manifest_hashes: dict[str, str] | None,
) -> bool:
    """Return True when *dest* is a user edit that must be preserved.

    *key* is the repo-relative path used as the ``content_hashes`` manifest key;
    *incoming* is the bundled content this update would write. Semantics come
    entirely from ``_version_manifest._is_user_modified``:

    - matches the incoming bundle → framework-managed, safe to rewrite;
    - matches the recorded manifest hash → TRW's own last write, safe to refresh
      with the new bundled content;
    - matches neither → a genuine user edit, preserve.

    A missing/corrupt manifest degrades to the *incoming bundle* baseline rather
    than to "overwrite", so a first-run or unreadable ``managed-artifacts.yaml``
    can never cost a user their edits.
    """
    return artifact_user_edited_against(dest, key, {hashlib.sha256(incoming).hexdigest()}, manifest_hashes)


@dataclass(frozen=True, slots=True)
class ManagedArtifactSource:
    """One client surface whose bundled content TRW owns and refreshes.

    Attributes:
        client: Client-profile id the surface belongs to (diagnostics only).
        surface: Repo-relative directory the artifacts live under, for logs.
        contents: Callable returning ``{repo-relative path: bundled bytes}`` for
            the CURRENT bundle. Registered from the generator's own content
            builder so the two can never drift.
    """

    client: str
    surface: str
    contents: Callable[[], dict[str, bytes]]


def _copilot_path_instructions() -> dict[str, bytes]:
    from ._copilot import copilot_path_instruction_contents

    return copilot_path_instruction_contents()


def _copilot_skills() -> dict[str, bytes]:
    from ._copilot import copilot_skill_contents

    return copilot_skill_contents()


def bundled_agent_contents(client: str) -> dict[str, bytes]:
    """``{repo-relative path: materialized bytes}`` for one client's agents.

    The SAME transform ``_install_one_agent`` applies, so the ownership guard
    compares against the exact bytes the installer would write. Derived from the
    bundled agent directory, so a twelfth specialist is covered with no edit
    here — the five hand-kept per-client template dictionaries this replaced are
    what let ``.github/agents`` and ``.cursor/agents`` drift from the bundle
    (PRD-CORE-252-FR04).

    An agent that cannot be materialized for *client* is omitted rather than
    recorded: claiming ownership of a path the installer never wrote is the
    failure direction that costs a user their file.
    """
    from trw_mcp.agents.agent_formats import agent_format_for
    from trw_mcp.agents.tier_resolver import materialize_agent
    from trw_mcp.exceptions import AgentFormatError

    from ._utils import _DATA_DIR

    try:
        fmt = agent_format_for(client)
    except AgentFormatError:
        return {}
    if not fmt.supports_agents:
        return {}
    source = _DATA_DIR / "agents"
    if not source.is_dir():
        return {}

    contents: dict[str, bytes] = {}
    for agent_file in sorted(source.glob("*.md")):
        try:
            rel = fmt.destination_for(agent_file.stem)
            contents[rel] = materialize_agent(agent_file.read_text(encoding="utf-8"), client=client).encode("utf-8")
        except (OSError, ValueError, AgentFormatError):
            logger.warning("bundled_agent_render_failed", agent=agent_file.name, client=client, exc_info=True)
    return contents


def _agent_source(client: str) -> Callable[[], dict[str, bytes]]:
    """Bind *client* into a zero-argument content callable for the registry."""
    return lambda: bundled_agent_contents(client)


def _agent_surface(client: str) -> str:
    """The repo-relative agent directory *client* installs into.

    ``ManagedArtifactSource.surface`` is not a label: every key a source yields
    must sit under it, and ``test_client_artifact_preservation`` asserts exactly
    that. A descriptive string here would pass import and fail that invariant.
    """
    from trw_mcp.agents.agent_formats import agent_format_for

    destination = agent_format_for(client).destination_dir
    if destination is None:  # unreachable: _AGENT_CLIENTS all have a surface
        raise ValueError(f"client {client!r} has no agent destination")
    return destination


def _cursor_commands() -> dict[str, bytes]:
    from ._cursor_ide import cursor_ide_command_contents

    return cursor_ide_command_contents()


def _cursor_skills() -> dict[str, bytes]:
    from ._cursor_ide import cursor_ide_skill_contents

    return cursor_ide_skill_contents()


# Surfaces whose per-file content hashes are persisted into
# ``managed-artifacts.yaml`` by THIS recorder. Codex (``.codex/agents``,
# ``.agents/skills``) and the ``.claude``/``.opencode`` core surfaces are absent
# because they have their own recorders — all three are registry members in
# ``_manifest_recorders.py`` and all three now decline user-edited artifacts.
#: Clients whose agent destination is recorded here. ``claude-code`` is absent
#: because ``.claude/agents`` has its own recorder, and that one accepts TWO
#: framework renderings (raw bundled tier form and resolved form) so a
#: mis-materialized agent can heal; every other client only ever received the
#: materialized form, so the single-hash guard is exactly right for them.
_AGENT_CLIENTS: tuple[str, ...] = ("antigravity-cli", "codex", "copilot", "cursor-ide", "opencode")

MANAGED_CLIENT_ARTIFACT_SOURCES: tuple[ManagedArtifactSource, ...] = (
    *(ManagedArtifactSource(client, _agent_surface(client), _agent_source(client)) for client in _AGENT_CLIENTS),
    ManagedArtifactSource("copilot", ".github/instructions", _copilot_path_instructions),
    ManagedArtifactSource("copilot", ".github/skills", _copilot_skills),
    ManagedArtifactSource("cursor-ide", ".cursor/commands", _cursor_commands),
    ManagedArtifactSource("cursor-ide", ".cursor/skills", _cursor_skills),
)


def managed_client_manifest_hashes(
    target_dir: Path,
    prev_hashes: dict[str, str] | None,
) -> dict[str, str]:
    """SHA256 of installed copilot/cursor/antigravity artifacts TRW still owns.

    Persisted into ``managed-artifacts.yaml`` so the NEXT update can tell a
    stale-but-unmodified artifact (refresh it) from a user edit (preserve it) —
    without which the guard would freeze every file at its first-installed
    content the moment the bundle changed.

    *prev_hashes* is the manifest as it stood BEFORE this run. An artifact that
    diverges from both the current bundle and its previous record is a user edit
    and is deliberately **omitted**: recording it would make TRW claim ownership
    of the user's content and overwrite it on the following update.
    """
    hashes: dict[str, str] = {}
    for source in MANAGED_CLIENT_ARTIFACT_SOURCES:
        try:
            contents = source.contents()
        except Exception:  # justified: a broken content source must not abort the manifest write
            logger.warning("managed_artifact_contents_failed", surface=source.surface, exc_info=True)
            continue
        for key, incoming in contents.items():
            dest = target_dir / key
            if not dest.is_file():
                continue
            if artifact_user_edited(dest, key, incoming, prev_hashes):
                logger.info("managed_artifact_ownership_declined", path=key, client=source.client)
                continue
            try:
                hashes[key] = hashlib.sha256(dest.read_bytes()).hexdigest()
            except OSError:
                logger.warning("managed_artifact_hash_failed", path=key)
    return hashes
