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

from trw_mcp.agents.agent_formats import agent_format_for
from trw_mcp.exceptions import AgentFormatError
from trw_mcp.models.config._profiles import builtin_client_ids

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


def _opencode_distill() -> dict[str, bytes]:
    from trw_mcp.channels.opencode._custom_commands import opencode_distill_command_contents
    from trw_mcp.channels.opencode._explorer_agent import EXPLORER_AGENT_RELPATH, get_explorer_agent_content

    return {
        **opencode_distill_command_contents(),
        EXPLORER_AGENT_RELPATH: get_explorer_agent_content().encode("utf-8"),
    }


def _claude_loop_md() -> dict[str, bytes]:
    """The bundled ``.claude/loop.md`` `/loop` customization (init-project step 7a-1).

    A plain byte-for-byte copy of ``_DATA_DIR/claude_code/loop.md`` -- no
    per-client rendering, unlike the agent templates above.
    """
    from ._utils import _DATA_DIR

    src = _DATA_DIR / "claude_code" / "loop.md"
    if not src.is_file():
        return {}
    return {".claude/loop.md": src.read_bytes()}


def _codex_post_edit_hook() -> dict[str, bytes]:
    """The bundled Codex PostToolUse distill-telemetry hook script."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import generate_hook_script

    return {".codex/hooks/trw_post_edit_telemetry.py": generate_hook_script().encode("utf-8")}


def _cursor_ide_hook_scripts() -> dict[str, bytes]:
    """The FR08 8-event ``.cursor/hooks/trw-*.sh`` adapter scripts (bundled, byte-for-byte)."""
    from ._cursor_hooks_io import _CURSOR_HOOKS_DATA_DIR
    from ._cursor_ide import _IDE_HOOK_SCRIPTS

    contents: dict[str, bytes] = {}
    for name in _IDE_HOOK_SCRIPTS:
        src = _CURSOR_HOOKS_DATA_DIR / name
        if src.is_file():
            contents[f".cursor/hooks/{name}"] = src.read_bytes()
    return contents


def _cursor_cli_default_config() -> dict[str, bytes]:
    """The default (never-yet-merged-with-a-user-file) ``.cursor/cli.json`` baseline.

    ``generate_cursor_cli_config`` smart-merges into an EXISTING file, so this
    is only the fresh-write candidate; a project whose file already carries
    user-added tokens diverges from it and is correctly treated as edited.
    """
    import json

    from ._cursor_cli import _DEFAULT_ALLOW, _DEFAULT_DENY

    default_config = {"permissions": {"allow": list(_DEFAULT_ALLOW), "deny": list(_DEFAULT_DENY)}}
    return {".cursor/cli.json": (json.dumps(default_config, indent=2) + "\n").encode("utf-8")}


def _copilot_hook_scripts() -> dict[str, bytes]:
    """The copilot hook adapter + C5 distill-hint scripts (bundled, byte-for-byte)."""
    from ._copilot import _COPILOT_ADAPTER_INSTALL_PATH, _bundled_adapter_script_path
    from ._copilot_distill_channels import _C5_HOOK_NAMES, _HOOKS_DATA_DIR

    contents: dict[str, bytes] = {}
    adapter_src = _bundled_adapter_script_path()
    if adapter_src.is_file():
        contents[_COPILOT_ADAPTER_INSTALL_PATH] = adapter_src.read_bytes()
    for name in _C5_HOOK_NAMES:
        src = _HOOKS_DATA_DIR / name
        if src.is_file():
            contents[f".github/hooks/{name}"] = src.read_bytes()
    return contents


def _antigravity_ceremony_rule() -> dict[str, bytes]:
    """The Antigravity CLI workspace rule (``.agents/rules/trw-ceremony.md``).

    Only the FRESH-write candidate: the writer smart-merges a start/end marker
    section into an existing file, so a project that already had the rule (and
    thus carries the merge, not a verbatim rewrite) is out of scope for this
    single-candidate guard the same way ``.claude/agents``' two renderings are
    handled separately -- a user edit outside the markers is preserved either
    way, since it would fail this byte-for-byte comparison.
    """
    from ._antigravity_cli import (
        _ANTIGRAVITY_RULE_FILENAME,
        _ANTIGRAVITY_RULES_DIR,
        _antigravity_instructions_content,
    )

    return {
        f"{_ANTIGRAVITY_RULES_DIR}/{_ANTIGRAVITY_RULE_FILENAME}": _antigravity_instructions_content().encode("utf-8")
    }


def _antigravity_before_edit_hook() -> dict[str, bytes]:
    """The AG-03 PreToolUse before-edit telemetry hook script.

    A live, security-relevant hook (PRD-CORE-... AG-03): recording its content
    hash is what lets a scoped or whole-project uninstall prove it may remove
    it, instead of leaving an active hook registered forever because the
    surface had no manifest coverage (rule 3 keeps what it cannot prove).
    """
    from trw_mcp.channels.antigravity._before_edit_hook import _AG03_HOOK_SCRIPT_PATH, generate_hook_script

    return {_AG03_HOOK_SCRIPT_PATH: generate_hook_script().encode("utf-8")}


def _cursor_rules_mdc_candidates() -> set[bytes]:
    """Both legitimate renderings of ``.cursor/rules/trw-ceremony.mdc``.

    The file is SHARED: cursor-ide writes the full protocol + appendix (the
    steady state after any ``update-project``, since only the cursor-ide writer
    runs on update), and cursor-cli writes a lighter body only at init time and
    only when cursor-ide was never selected for the project (see
    ``_install_cursor_cli_artifacts``). Both are TRW's own content -- a single
    hash here would misclassify whichever one is not currently on disk as a
    user edit. A candidate that fails to render is skipped, never treated as a
    match.
    """
    from ._cursor import cursor_rules_mdc_body
    from ._cursor_cli import _cursor_cli_trw_section
    from ._ide_targets import _extract_trw_section_content

    candidates: set[bytes] = set()
    try:
        candidates.add(cursor_rules_mdc_body(_extract_trw_section_content(), "cursor-ide").encode("utf-8"))
    except Exception:  # justified: fail-open, this candidate is skipped rather than crashing the sweep
        logger.warning("cursor_rules_mdc_ide_candidate_failed", exc_info=True)
    try:
        candidates.add(cursor_rules_mdc_body(_cursor_cli_trw_section(), "cursor-cli").encode("utf-8"))
    except Exception:  # justified: fail-open, this candidate is skipped rather than crashing the sweep
        logger.warning("cursor_rules_mdc_cli_candidate_failed", exc_info=True)
    return candidates


def _cursor_rules_mdc_manifest_hash(target_dir: Path, prev_hashes: dict[str, str] | None) -> dict[str, str]:
    """Content hash for ``.cursor/rules/trw-ceremony.mdc``, or ``{}`` when absent/edited.

    Handled outside :data:`MANAGED_CLIENT_ARTIFACT_SOURCES` because that
    registry's ``contents()`` callables yield exactly one candidate per key;
    this surface legitimately has two (see :func:`_cursor_rules_mdc_candidates`).
    """
    key = ".cursor/rules/trw-ceremony.mdc"
    dest = target_dir / key
    if not dest.is_file():
        return {}
    candidates = _cursor_rules_mdc_candidates()
    if not candidates:
        return {}
    framework_hashes = {hashlib.sha256(c).hexdigest() for c in candidates}
    if artifact_user_edited_against(dest, key, framework_hashes, prev_hashes):
        logger.info("managed_artifact_ownership_declined", path=key, client="cursor")
        return {}
    try:
        return {key: hashlib.sha256(dest.read_bytes()).hexdigest()}
    except OSError:  # trw-fail-silent-allow: mirrors the sibling sweep's unreadable-file handling; omission is the safe direction (no manifest entry means "unproven", never "TRW's")
        logger.warning("managed_artifact_hash_failed", path=key)
        return {}


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
#:
#: An entry here is a DECLARED exclusion, not an omission — the membership below
#: is derived, so the only way out of the recorder is to be named here with a
#: reason.
_AGENT_RECORDER_EXCLUSIONS: dict[str, str] = {
    "claude-code": ".claude/agents has its own recorder in _manifest_recorders.py",
}


#: DERIVED (2026-09-12) from the profile registry intersected with "has a
#: documented agent surface" (``agent_format_for(...).supports_agents``, which
#: is what excludes ``cursor-cli``), minus the declared exclusions above.
#:
#: It was a hand-written 5-tuple. A new profile with an agent surface would have
#: been absent from it, and absence is silent here: the client gets no
#: per-file hash record, so ``artifact_user_edited`` cannot tell TRW's own last
#: write from the user's hand edit and ``update-project`` overwrites the user's
#: agent files (CONSTITUTION HB-2 — the exact class this module exists to
#: prevent, re-entering through the enumeration rather than the predicate).
#: ``sorted`` preserves the previous ordering of
#: ``MANAGED_CLIENT_ARTIFACT_SOURCES`` exactly.
def _derive_agent_clients() -> tuple[str, ...]:
    """Profiles with an agent surface this recorder owns. Fails closed, loudly.

    An ``AgentFormatError`` here means a profile exists in the client registry
    with no entry in ``agents/agent_formats._REGISTRY``. It is re-raised rather
    than skipped: skipping would drop the client out of the recorder silently,
    which is the data-losing direction (its agent files become overwritable).
    """
    derived: list[str] = []
    for client in builtin_client_ids():
        if client in _AGENT_RECORDER_EXCLUSIONS:
            continue
        try:
            if agent_format_for(client).supports_agents:
                derived.append(client)
        except AgentFormatError as exc:  # pragma: no cover - registry inconsistency
            raise RuntimeError(
                f"client profile {client!r} has no agents/agent_formats._REGISTRY entry, so its "
                "agent surface cannot be classified. Add the entry (an unsupported surface is "
                "declared with unsupported_reason) — leaving it out disables the user-edit guard "
                "for that client."
            ) from exc
    if not derived:  # pragma: no cover - floor, see builtin_client_ids()
        raise RuntimeError(
            "_AGENT_CLIENTS derived empty: every client-profile agent surface resolved unsupported. "
            "An empty recorder set silently disables the user-edit guard for every client."
        )
    return tuple(sorted(derived))


_AGENT_CLIENTS: tuple[str, ...] = _derive_agent_clients()

MANAGED_CLIENT_ARTIFACT_SOURCES: tuple[ManagedArtifactSource, ...] = (
    *(ManagedArtifactSource(client, _agent_surface(client), _agent_source(client)) for client in _AGENT_CLIENTS),
    ManagedArtifactSource("copilot", ".github/instructions", _copilot_path_instructions),
    ManagedArtifactSource("copilot", ".github/skills", _copilot_skills),
    ManagedArtifactSource("cursor-ide", ".cursor/commands", _cursor_commands),
    ManagedArtifactSource("cursor-ide", ".cursor/skills", _cursor_skills),
    # PRD-INFRA-192 FR12: the opencode distill installer used to keep its own
    # hashes in the manifest, which _write_manifest then dropped, so every update
    # overwrote a user's edit to these files.
    ManagedArtifactSource("opencode", ".opencode", _opencode_distill),
    # PRD-INFRA-192 FR09 C3: recorder-coverage gaps closed so a scoped uninstall
    # can tell TRW's own unedited write from a user's file under these surfaces,
    # instead of the pre-C3 fallback of wholesale-deleting the whole directory.
    ManagedArtifactSource("claude-code", ".claude", _claude_loop_md),
    ManagedArtifactSource("codex", ".codex/hooks", _codex_post_edit_hook),
    ManagedArtifactSource("antigravity-cli", ".antigravitycli/hooks", _antigravity_before_edit_hook),
    ManagedArtifactSource("cursor-ide", ".cursor/hooks", _cursor_ide_hook_scripts),
    ManagedArtifactSource("cursor-cli", ".cursor", _cursor_cli_default_config),
    ManagedArtifactSource("copilot", ".github/hooks", _copilot_hook_scripts),
    ManagedArtifactSource("antigravity-cli", ".agents/rules", _antigravity_ceremony_rule),
)


def _core_root_file_contents() -> dict[str, bytes]:
    """Bundled bytes for TRW's own root-level singleton files.

    ``REVIEW.md`` and the canon docs (``FRAMEWORK.md``, ``AARE-F-FRAMEWORK.md``)
    have no per-client directory (they live in ``_CORE_SURFACES``, not
    ``_PROFILE_DIR_SURFACES``), so they cannot be registered as a
    :class:`ManagedArtifactSource` -- ``test_every_source_key_is_repo_relative``
    requires every source key to sit under a directory-shaped ``surface``, and a
    root file has none. :func:`bundled_content_for` merges this in instead, which
    is what lets a root file the user pre-authored (Claude Code's own review
    config, say) be told apart from TRW's own unedited write (PRD-INFRA-192 FR09
    C3 review follow-up).
    """
    from trw_mcp.canons._views import install_view
    from trw_mcp.canons.registry import load_registry

    from ._config_templates import _minimal_review_md
    from ._utils import _DATA_DIR

    contents: dict[str, bytes] = {"REVIEW.md": _minimal_review_md().encode("utf-8")}
    for resource, destination in install_view(load_registry()):
        if "/" in destination:
            continue  # a .trw/frameworks/* mirror, not a root file
        src = _DATA_DIR / resource
        if src.is_file():
            contents[destination] = src.read_bytes()
    return contents


def bundled_content_for(relpath: str) -> bytes | None:
    """Best-effort bundled-source lookup for a plain surface with NO manifest coverage.

    The generic half of ``_uninstall_manifest.plan_uncovered_surface``: scans
    every registered content source's CURRENT output for an exact key match,
    so a single-file surface can be recognized as TRW's own unedited write
    without a per-file literal in the uninstall handler. Returns ``None`` when
    no registered source produces *relpath* -- the caller's fail-safe direction
    is to keep the file in that case, never to guess.
    """
    for source in MANAGED_CLIENT_ARTIFACT_SOURCES:
        try:
            contents = source.contents()
        except Exception:  # justified: a broken content source must not block the uninstall decision
            logger.warning("bundled_content_lookup_failed", surface=source.surface, exc_info=True)
            continue
        if relpath in contents:
            return contents[relpath]
    try:
        return _core_root_file_contents().get(relpath)
    except Exception:  # trw-fail-silent-allow: None means the caller keeps the file; a broken lookup never deletes
        logger.warning("core_root_file_lookup_failed", relpath=relpath, exc_info=True)
        return None


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
    hashes.update(_cursor_rules_mdc_manifest_hash(target_dir, prev_hashes))
    return hashes
