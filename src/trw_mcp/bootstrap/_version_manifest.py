"""Manifest read + content-hash helpers — extracted from _version_migration.py.

Belongs to the ``_version_migration.py`` facade. Re-exported there for
backward compatibility with callers that import via the parent (``_update_project.py``,
test modules, ``bootstrap/__init__.py``).

Self-contained:
- ``_MANIFEST_FILE`` — name of the managed-artifacts manifest in .trw/
- ``_coerce_manifest_list`` — coerce a manifest field to list[str]
- ``_read_manifest`` — load + normalize the manifest YAML
- ``_compute_content_hashes`` — SHA256 of installed artifact files
- ``_render_agent`` / ``_framework_agent_hashes`` — resolve a bundled agent's
  capability-tier ``model:`` line + hash its framework renderings
- ``_is_user_modified`` — modification guard (moved here from ``_template_updater``)
- ``_apply_agent_update`` — resolve-and-write an agent on update-project so the
  ``model:`` tier token is materialized exactly like fresh install
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import structlog

from trw_mcp.agents.tier_resolver import materialize_agent
from trw_mcp.exceptions import StateError

from ._file_ops import ProgressCallback

logger = structlog.get_logger(__name__)

_MANIFEST_FILE = "managed-artifacts.yaml"


#: The one schema version this build reads and writes; there is no old-schema reader.
MANIFEST_VERSION = 2
_CLEAN_REINSTALL = "clean reinstall: `trw-mcp uninstall --keep-memory` then `trw-mcp init-project`"

#: Distributions whose resolved version is recorded in the manifest's ``packages``
#: map (PRD-INFRA-192 FR12). The manifest is the ONE record of what was
#: installed; ``version-status`` reads it back rather than a VERSION.yaml stamp.
_MANIFEST_PACKAGE_DISTRIBUTIONS: tuple[str, ...] = ("trw-mcp", "trw-memory")


def resolved_package_versions() -> dict[str, str]:
    """Resolve each managed distribution's version in THIS interpreter.

    Uses ``importlib.metadata`` against the interpreter running init-project/
    update-project — the target's own interpreter — so the recorded version is
    what is actually installed there, not a bundle constant. A distribution
    that is not installed (``PackageNotFoundError``) is omitted rather than
    recorded as ``"unknown"``, so a caller can distinguish "not installed" from
    "installed but the manifest is stale".
    """
    import importlib.metadata

    versions: dict[str, str] = {}
    for distribution in _MANIFEST_PACKAGE_DISTRIBUTIONS:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:  # trw-fail-silent-allow: an uninstalled distribution is omitted, which version-status reads as "not installed"
            continue
    return versions


def manifest_refusal(target_dir: Path) -> str | None:
    """Why an existing installation must not be updated, or ``None`` when its manifest is usable.

    Without a readable current-schema manifest TRW cannot tell which files it
    wrote, so an update would guess ownership. It refuses instead and names the
    clean reinstall, which keeps the learning corpus (PRD-INFRA-192-NFR02).
    """
    from trw_mcp.state.persistence import FileStateReader

    manifest_path = target_dir / ".trw" / _MANIFEST_FILE
    try:
        data = FileStateReader().read_yaml(manifest_path)
    except StateError as exc:
        why = "is missing" if not manifest_path.is_file() else f"is unreadable ({exc})"
    else:
        # ``type() is int``: YAML ``2.0`` and ``true`` compare equal to an int but are not one.
        if type(data.get("version")) is not int or data.get("version") != MANIFEST_VERSION:
            why = f"has unsupported schema version {data.get('version')!r} (expected {MANIFEST_VERSION})"
        elif (field := _malformed_manifest_field(data)) is None:
            return None
        else:
            why = f"has a malformed {field!r} field"
    return f"refusing to update: {manifest_path} {why}, so TRW cannot tell which files it owns; {_CLEAN_REINSTALL}"


_MANIFEST_LIST_FIELDS = (
    "skills",
    "agents",
    "hooks",
    "opencode_commands",
    "opencode_agents",
    "opencode_skills",
    "custom_skills",
    "custom_agents",
    "custom_hooks",
    "custom_opencode_commands",
    "custom_opencode_agents",
    "custom_opencode_skills",
)


def _is_str_map(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())


def _is_owners_map(value: object) -> bool:
    """True for ``dict[str, list[str]]`` -- the ``owners`` field's required shape."""
    return isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, list) and all(isinstance(item, str) for item in v)
        for k, v in value.items()
    )


def _is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _malformed_manifest_field(data: dict[str, object]) -> str | None:
    """The first field whose type is wrong, or ``None``; the reader would otherwise coerce it to empty.

    ``content_hashes`` is required by schema 2. A list field, ``packages``,
    ``owners``, or ``tombstones`` (PRD-INFRA-192 FR12) may be absent (older
    writers omitted them) but never the wrong type.
    """
    if not _is_str_map(data.get("content_hashes")):
        return "content_hashes"
    for field in _MANIFEST_LIST_FIELDS:
        value = data.get(field, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return field
    if "packages" in data and not _is_str_map(data["packages"]):
        return "packages"
    if "owners" in data and not _is_owners_map(data["owners"]):
        return "owners"
    return "tombstones" if "tombstones" in data and not _is_str_list(data["tombstones"]) else None


def _coerce_manifest_list(value: object) -> list[str]:
    """Coerce a manifest field to ``list[str]``, returning ``[]`` for non-lists."""
    return [str(item) for item in value] if isinstance(value, list) else []


def _read_manifest(target_dir: Path) -> dict[str, object] | None:
    """Read the managed-artifacts manifest from a target project.

    Returns ``None`` if the manifest does not exist (first update after
    manifest support was added).
    """
    manifest_path = target_dir / ".trw" / _MANIFEST_FILE
    if not manifest_path.exists():
        return None
    try:
        from trw_mcp.state.persistence import FileStateReader

        reader = FileStateReader()
        data = reader.read_yaml(manifest_path)
        if not isinstance(data, dict):
            return None
        result: dict[str, object] = {key: _coerce_manifest_list(data.get(key, [])) for key in _MANIFEST_LIST_FIELDS}
        raw_version = data.get("version", 1)
        result["version"] = raw_version if type(raw_version) is int else 0
        raw_hashes = data.get("content_hashes")
        if isinstance(raw_hashes, dict):
            result["content_hashes"] = {str(k): str(v) for k, v in raw_hashes.items()}
        else:
            result["content_hashes"] = {}
        raw_owners = data.get("owners")
        if isinstance(raw_owners, dict):
            result["owners"] = {
                str(k): ([str(item) for item in v] if isinstance(v, list) else []) for k, v in raw_owners.items()
            }
        else:
            result["owners"] = {}
        raw_tombstones = data.get("tombstones")
        result["tombstones"] = [str(item) for item in raw_tombstones] if isinstance(raw_tombstones, list) else []
        return result
    except (OSError, StateError):  # trw-fail-silent-allow: logged; update refuses before reaching here
        # update_project never gets here with an invalid manifest: manifest_refusal
        # stops it first (PRD-INFRA-192-NFR02). This path is init_project over a
        # target whose old manifest it is about to replace.
        logger.warning("manifest_read_failed", path=str(manifest_path))
        return None


def _instruction_file_baselines(target_dir: Path) -> list[tuple[str, Path, set[str]]]:
    """Framework baselines for the two whole-file client instruction surfaces.

    ``.opencode/INSTRUCTIONS.md`` and ``.codex/INSTRUCTIONS.md`` have no bundled
    file — TRW renders them whole — so their "what TRW would write" baseline is
    the rendering itself, exactly as ``_opencode_instructions`` computes it on the
    write side. A render failure yields NO entry, which fails toward preservation
    (PRD-FIX-121-NFR04).
    """
    from trw_mcp.state.claude_md._instruction_clients import _detect_opencode_model_family
    from trw_mcp.state.claude_md._static_sections import render_codex_instructions, render_opencode_instructions

    renderers: tuple[tuple[str, Callable[[], str]], ...] = (
        (".opencode/INSTRUCTIONS.md", lambda: render_opencode_instructions(_detect_opencode_model_family(target_dir))),
        (".codex/INSTRUCTIONS.md", render_codex_instructions),
    )
    entries: list[tuple[str, Path, set[str]]] = []
    for rel, render in renderers:
        try:
            rendered = render()
        except Exception:  # justified: an unrenderable baseline must not abort the manifest write
            logger.warning("instruction_baseline_render_failed", path=rel)
            continue
        entries.append((rel, target_dir / rel, {hashlib.sha256(rendered.encode("utf-8")).hexdigest()}))
    return entries


def _core_artifact_baselines(
    target_dir: Path,
    bundled: dict[str, list[str]],
    data_dir: Path | None,
) -> list[tuple[str, Path, set[str]]]:
    """``(manifest key, installed path, framework-hash baseline)`` per core artifact.

    The baseline MUST be the same one the WRITER of that artifact consults, or
    the recorder and the guard disagree about what "TRW's own content" means.
    Agents are the reason this returns a hash SET: ``_apply_agent_update``
    accepts both the raw bundled tier form and the resolved form
    (:func:`_framework_agent_hashes`), so a recorder that only knew the raw bytes
    would decline every healthy agent and freeze them all once the bundle moved
    on — the RISK-001 over-correction PRD-FIX-121 explicitly forbids.

    ``AGENTS.md`` is deliberately absent. It is a marker-merged SHARED file: TRW
    owns one block, the user owns the rest, so a whole-file ownership hash is
    meaningless there. It was recorded before PRD-FIX-121 and never read — its
    writer (``state.claude_md._agents_md``) does a marker merge and consults no
    content hash — so recording it only kept a hash of the user's prose in a map
    labelled "what TRW wrote".
    """
    from ._utils import _DATA_DIR

    effective = data_dir or _DATA_DIR
    claude = target_dir / ".claude"
    opencode_src = effective / "opencode"
    opencode = target_dir / ".opencode"
    entries: list[tuple[str, Path, set[str]]] = [
        (name, claude / "agents" / name, _framework_agent_hashes(effective / "agents" / name, client="claude-code"))
        for name in bundled.get("agents", [])
    ]
    entries += [
        (name, claude / "hooks" / name, _framework_content_hashes(effective / "hooks" / name))
        for name in bundled.get("hooks", [])
    ]
    entries += [
        (
            f"{name}/SKILL.md",
            claude / "skills" / name / "SKILL.md",
            _framework_content_hashes(effective / "skills" / name / "SKILL.md"),
        )
        for name in bundled.get("skills", [])
    ]
    # ``agents`` is deliberately absent from this loop since PRD-CORE-252: every
    # non-claude-code client's agents are materialized from the shared bundle and
    # recorded by ``_managed_client_artifacts.bundled_agent_contents``, which
    # compares against the bytes the installer actually writes. Baselining them
    # against a retired ``data/opencode/agents`` directory would report every
    # healthy opencode agent as having no framework baseline.
    for kind in ("commands",):
        entries += [
            (
                f".opencode/{kind}/{name}",
                opencode / kind / name,
                _framework_content_hashes(opencode_src / kind / name),
            )
            for name in bundled.get(f"opencode_{kind}", [])
        ]
    entries += _opencode_skill_baselines(opencode / "skills", effective / "skills", bundled.get("opencode_skills", []))
    return entries + _instruction_file_baselines(target_dir)


def _compute_content_hashes(
    target_dir: Path,
    bundled: dict[str, list[str]],
    prev_hashes: dict[str, str] | None = None,
    data_dir: Path | None = None,
) -> dict[str, str]:
    """Compute SHA256 hashes of installed ``.claude``/``.opencode``/``.codex`` artifacts.

    PRD-FIX-068-FR04: Hashes enable drift detection between installed
    copies and the current bundle.

    PRD-FIX-121-FR01: an artifact the user has edited is **omitted** — not
    recorded as ``null``, not recorded with a sentinel. *prev_hashes* is the
    manifest as it stood BEFORE this run; content matching neither the current
    bundle nor its own previous record is the user's, and recording it would make
    the NEXT update read the user's own hash back as "TRW wrote this" and
    overwrite it. Omission is what makes ``_is_user_modified`` fall through to the
    framework baseline and preserve again.
    """
    from ._managed_client_artifacts import artifact_user_edited_against

    hashes: dict[str, str] = {}
    for key, path, framework_hashes in _core_artifact_baselines(target_dir, bundled, data_dir):
        if not path.is_file():
            continue
        if not framework_hashes:
            # The bundled source was unreadable, so ownership is undecidable.
            # Fail toward the user (no entry), never toward "TRW owns it" —
            # that error path is how the defect would come back (NFR04).
            logger.warning("manifest_baseline_unavailable", path=key)
            continue
        if artifact_user_edited_against(path, key, framework_hashes, prev_hashes):
            logger.info("manifest_ownership_declined", path=key)
            continue
        try:
            hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            logger.warning("content_hash_failed", path=str(path))
    return hashes


def _manifest_content_hashes(
    prev_manifest: dict[str, object] | None,
) -> dict[str, str] | None:
    """Extract the ``content_hashes`` map from a prior manifest, or ``None``.

    Returns ``None`` on a first-run/no-manifest project (``prev_manifest is
    None``) or when the manifest predates the content-hash schema
    (PRD-FIX-068-FR04). The ``str``-coercion keeps the return type strict for
    the user-modification guard (:func:`_is_user_modified`).
    """
    if not isinstance(prev_manifest, dict):
        return None
    hashes = prev_manifest.get("content_hashes")
    if not isinstance(hashes, dict):
        return None
    return {str(k): str(v) for k, v in hashes.items()}


# ---------------------------------------------------------------------------
# Agent materialization (resolve capability-tier ``model:`` line on update)
# ---------------------------------------------------------------------------


def _render_agent(src: Path, *, client: str) -> str | None:
    """Return the resolved text of a bundled agent, or ``None`` on failure.

    Applies the same bundle→installed transform the installer uses
    (:func:`materialize_agent`: tool-placeholder rendering plus capability-tier
    resolution), so an already-current agent never reports as a pending update.
    Returns ``None`` when the source is unreadable, the tier is unknown for
    *client*, or the bundled frontmatter cannot be translated into that
    client's agent format (mirrors the per-agent skip-on-error semantics of
    ``_install_one_agent``).
    """
    from trw_mcp.exceptions import AgentFormatError

    try:
        raw = src.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return materialize_agent(raw, client=client)
    except (ValueError, AgentFormatError):
        return None


def _framework_agent_hashes(src: Path, *, client: str) -> set[str]:
    """Return SHA256 hashes of every *framework* rendering of agent *src*.

    An on-disk agent is unmodified/framework-managed when it matches EITHER the
    raw bundled tier form (``model: frontier`` — what a pre-fix update wrote) OR
    the resolved form (``model: opus`` — what fresh install writes). Recognising
    both lets a previously mis-materialized agent self-heal without being
    misclassified as a user edit. Returns an empty set when *src* is unreadable.
    """
    hashes: set[str] = set()
    try:
        hashes.add(hashlib.sha256(src.read_bytes()).hexdigest())
    except OSError:
        return hashes
    resolved = _render_agent(src, client=client)
    if resolved is not None:
        hashes.add(hashlib.sha256(resolved.encode("utf-8")).hexdigest())
    return hashes


def _opencode_skill_baselines(installed: Path, corpus: Path, names: list[str]) -> list[tuple[str, Path, set[str]]]:
    """Every file ``install_opencode_skills`` writes for *names* -- ``skill_files``, the writer's own list."""
    from ._client_skills import skill_files

    entries: list[tuple[str, Path, set[str]]] = []
    for name in names:
        try:
            files = skill_files("opencode", name, root=corpus)
        except (
            OSError
        ):  # trw-fail-silent-allow: an unreadable bundled skill has no baseline, like _framework_content_hashes
            continue
        entries += [
            (f".opencode/skills/{name}/{filename}", installed / name / filename, {hashlib.sha256(data).hexdigest()})
            for filename, data in files
        ]
    return entries


def _framework_content_hashes(src: Path) -> set[str]:
    """Return the SHA256 of bundled source *src* as a single-element baseline set.

    Analogous to :func:`_framework_agent_hashes` but for artifacts that are NOT
    tier-resolved (hooks, skills): their only framework rendering is the raw
    bundled file content, so a one-element set suffices. Supplying this baseline
    to :func:`_is_user_modified` makes "matches shipped content" vs "diverged"
    decidable even when ``managed-artifacts.yaml`` is missing, corrupt, or
    predates the content-hash schema — closing the FR05 gap where a user-edited
    hook/skill was silently clobbered because only agents carried a baseline.
    Returns an empty set when *src* is unreadable (falls back to legacy behavior).
    """
    try:
        return {hashlib.sha256(src.read_bytes()).hexdigest()}
    except OSError:
        return set()


def _is_user_modified(
    dest: Path,
    name: str,
    manifest_hashes: dict[str, str] | None,
    *,
    framework_hashes: set[str] | None = None,
) -> bool:
    """Check if an installed file was modified by the user since last install.

    PRD-FIX-068-FR05: compares the current on-disk SHA256 against the stored
    manifest hash; a mismatch means the user edited the file, so it is preserved.

    Two independent "known-good" baselines are consulted, in priority order:

    1. *framework_hashes* (sub_5ctrrLJ) — the update path resolves an agent's
       ``model:`` tier line before writing, so a healthy on-disk agent is the
       *resolved* form while a legacy/stale manifest may store the *raw tier*
       hash (or vice versa). A dest matching ANY known framework rendering is
       treated as unmodified (updatable) regardless of the manifest — otherwise
       a resolved-but-unmodified agent would be frozen at a broken
       ``model: frontier`` forever.
    2. *manifest_hashes[name]* — what the last install/update wrote; a drift
       from it means the user edited the file since.

    Pre-manifest fallback (P1-7 round-2 audit): when there is NO manifest record
    for *name* (first update, or a project installed before manifest support) but
    *framework_hashes* IS supplied and the dest matched none of them above, the
    file is a genuine user edit and MUST be preserved — FR05's acceptance is
    unconditional. Without any framework baseline the two forms are
    indistinguishable, so we fall back to the legacy "update" behavior.
    """
    if not dest.is_file():
        return False
    try:
        current_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
    except OSError:
        return False
    # Baseline 1: matches a framework rendering → framework-managed, safe to update.
    if framework_hashes and current_hash in framework_hashes:
        return False
    # Baseline 2: recorded manifest hash → modified iff the on-disk content drifted.
    if manifest_hashes and name in manifest_hashes:
        return current_hash != manifest_hashes[name]
    # No manifest record: a framework baseline that the dest failed to match
    # means a genuine user edit → preserve. No baseline at all → legacy update.
    return bool(framework_hashes)


def _apply_agent_update(
    agent_file: Path,
    dest: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback,
    manifest_hashes: dict[str, str] | None,
    *,
    client: str = "claude-code",
    manifest_key: str | None = None,
) -> None:
    """Materialize one agent on update-project, resolving its ``model:`` tier.

    sub_5ctrrLJ root cause: update-project used to raw-``copy2`` bundled agents,
    re-introducing the unresolvable ``model: frontier`` token and breaking agent
    spawns after every upgrade. This routes the write through the SAME
    resolve-and-write path as fresh install (:func:`_install_one_agent`) so
    ``frontier`` becomes ``opus`` (etc.), while preserving genuinely user-edited
    agents via :func:`_is_user_modified`.

    *manifest_key* is the ``content_hashes`` key for this destination. It
    defaults to the bare bundled filename, which is the historical key for
    ``.claude/agents``; every other client passes its repo-relative destination
    so two clients' copies of the same agent cannot collide on one key
    (PRD-CORE-252-FR03).
    """
    from ._init_project_skills import _install_one_agent

    key = manifest_key or agent_file.name
    framework_hashes = _framework_agent_hashes(agent_file, client=client)
    # `.claude/agents` records under the bare filename; looking it up by path
    # found no record, so every bundle change to a claude-code agent was
    # preserved as a "user edit" and never landed (PRD-INFRA-190 FR02 scenario 2).
    if _is_user_modified(dest, _manifest_key_for(key), manifest_hashes, framework_hashes=framework_hashes):
        logger.info("artifact_user_modified", path=str(dest))
        result.setdefault("modified", []).append(str(dest))
        # Also recorded under ``preserved``, which is the bucket the CLI summary
        # counts ("N preserved"). ``modified`` is read by callers that need the
        # absolute path and is surfaced nowhere, so recording only there made a
        # preserved user edit invisible to the person whose edit it was.
        result.setdefault("preserved", []).append(key)
        return

    resolved = _render_agent(agent_file, client=client)
    if resolved is not None and dest.is_file() and dest.read_text(encoding="utf-8") == resolved:
        return
    existed = dest.exists()
    error_count = len(result["errors"])
    # A scratch dict: this function writes; the update reports from the diff.
    scratch = {"created": [], "errors": result["errors"]}
    _install_one_agent(agent_file, dest, force=True, result=scratch, on_progress=None, client=client)
    if on_progress:
        on_progress("Error" if len(result["errors"]) > error_count else "Updated" if existed else "Created", str(dest))


# ---------------------------------------------------------------------------
# Uncommitted-change ownership (PRD-INFRA-190 FR04/FR05/NFR01)
# ---------------------------------------------------------------------------

#: ``content_hashes`` keys for these surfaces are the path BELOW the directory
#: (a bare agent/hook filename, ``<skill>/<file>``); every other key is the
#: repo-relative path itself.
_BARE_KEY_DIRS: tuple[str, ...] = (".claude/agents/", ".claude/hooks/", ".claude/skills/")


def _manifest_key_for(rel: str) -> str:
    """The ``content_hashes`` key under which repo-relative *rel* is recorded."""
    for prefix in _BARE_KEY_DIRS:
        if rel.startswith(prefix):
            return rel.removeprefix(prefix)
    return rel


def _manifest_key_path(key: str) -> str:
    """The repo-relative path a ``content_hashes`` key records (inverse of :func:`_manifest_key_for`)."""
    if key.startswith("."):
        return key
    if "/" in key:
        return f".claude/skills/{key}"
    return f".claude/hooks/{key}" if key.endswith(".sh") else f".claude/agents/{key}"


def git_dirty_paths(target_dir: Path, pathspecs: list[str]) -> set[str] | None:
    """Repo-relative paths git reports modified, staged, added, renamed or untracked.

    ``None`` means git could not answer (missing binary, timeout, not a work
    tree): the caller reports ``unknown`` and falls back to the manifest guard
    (NFR01). Ignored files are never dirty — they stay under the manifest guard.
    """
    import os
    import subprocess

    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(target_dir.parent), "GIT_OPTIONAL_LOCKS": "0"}
    command = ["git", "-C", str(target_dir), "status", "--porcelain=v1", "-z", "--untracked-files=all", "--"]
    try:
        proc = subprocess.run([*command, *pathspecs], capture_output=True, timeout=5, env=env, check=False)  # noqa: S603
    except (
        OSError,
        subprocess.TimeoutExpired,
    ):  # trw-fail-silent-allow: None is "unknown", which the caller reports as a warning (NFR01)
        logger.warning("git_dirty_check_unavailable", target=str(target_dir), exc_info=True)
        return None
    if proc.returncode != 0:
        logger.warning("git_dirty_check_failed", target=str(target_dir), returncode=proc.returncode)
        return None
    dirty: set[str] = set()
    fields = iter(proc.stdout.split(b"\0"))
    for entry in fields:
        if len(entry) < 4:
            continue
        dirty.add(entry[3:].decode("utf-8", "surrogateescape").rstrip("/"))
        if entry[:1] in (b"R", b"C"):
            dirty.add(next(fields, b"").decode("utf-8", "surrogateescape"))
    return dirty


def preserve_uncommitted_changes(
    target_dir: Path,
    snapshot_root: Path,
    dirty: set[str],
    manifest_hashes: dict[str, str] | None,
    result: dict[str, list[str]],
) -> None:
    """Undo every write to a dirty path whose pre-run bytes TRW did not record.

    Runs after the writers and before the manifest is recorded, so the
    ownership recorders see the preserved bytes. A dirty path whose pre-run
    bytes hash to its ``content_hashes`` record is TRW's own last write and
    keeps the refresh.
    """
    from ._update_transaction import _file_signature, _restore_transaction_file

    for rel in sorted(dirty):
        before, after = snapshot_root / rel, target_dir / rel
        if _file_signature(before) == _file_signature(after):
            continue
        if before.is_file() and not before.is_symlink():
            recorded = (manifest_hashes or {}).get(_manifest_key_for(rel))
            if recorded == hashlib.sha256(before.read_bytes()).hexdigest():
                continue
        _restore_transaction_file(target_dir, snapshot_root, rel)
        result.setdefault("preserved", []).append(f"{rel} (uncommitted_changes)")
