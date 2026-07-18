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
from pathlib import Path

import structlog

from trw_mcp.agents.tier_resolver import rewrite_model_line
from trw_mcp.exceptions import StateError

from ._file_ops import ProgressCallback

logger = structlog.get_logger(__name__)

_MANIFEST_FILE = "managed-artifacts.yaml"


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
        result: dict[str, object] = {
            key: _coerce_manifest_list(data.get(key, []))
            for key in (
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
        }
        raw_version = data.get("version", 1)
        result["version"] = int(str(raw_version))
        raw_hashes = data.get("content_hashes")
        if isinstance(raw_hashes, dict):
            result["content_hashes"] = {str(k): str(v) for k, v in raw_hashes.items()}
        else:
            result["content_hashes"] = {}
        return result
    except (OSError, StateError):
        # A corrupted/malformed managed-artifacts.yaml (StateError from
        # FileStateReader.read_yaml) must NOT crash update_project — degrade to
        # "no prior manifest" so the update proceeds (agents self-heal, hooks/
        # skills fall back to the framework-baseline guard). P2-3 round-2 audit.
        logger.warning("manifest_read_failed", path=str(manifest_path))
        return None


def _compute_content_hashes(
    target_dir: Path,
    bundled: dict[str, list[str]],
) -> dict[str, str]:
    """Compute SHA256 hashes of installed artifact files.

    PRD-FIX-068-FR04: Hashes enable drift detection between installed
    copies and the current bundle.
    """
    hashes: dict[str, str] = {}

    def _record_hash(path: Path, key: str) -> None:
        try:
            if path.is_file():
                hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            logger.warning("content_hash_failed", path=str(path))

    for name in bundled["agents"]:
        _record_hash(target_dir / ".claude" / "agents" / name, name)
    for name in bundled["hooks"]:
        _record_hash(target_dir / ".claude" / "hooks" / name, name)
    for name in bundled["skills"]:
        _record_hash(target_dir / ".claude" / "skills" / name / "SKILL.md", f"{name}/SKILL.md")
    for name in bundled.get("opencode_commands", []):
        _record_hash(target_dir / ".opencode" / "commands" / name, f".opencode/commands/{name}")
    for name in bundled.get("opencode_agents", []):
        _record_hash(target_dir / ".opencode" / "agents" / name, f".opencode/agents/{name}")
    for name in bundled.get("opencode_skills", []):
        _record_hash(target_dir / ".opencode" / "skills" / name / "SKILL.md", f".opencode/skills/{name}/SKILL.md")
    _record_hash(target_dir / ".opencode" / "INSTRUCTIONS.md", ".opencode/INSTRUCTIONS.md")
    _record_hash(target_dir / ".codex" / "INSTRUCTIONS.md", ".codex/INSTRUCTIONS.md")
    _record_hash(target_dir / "AGENTS.md", "AGENTS.md")
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

    The bundled agent declares a capability tier (``model: frontier``); this
    rewrites it to the model id *client* accepts (``model: opus`` for
    ``claude-code``) via :func:`rewrite_model_line`. Returns ``None`` when the
    source is unreadable or the tier is unknown for *client* (mirrors the
    per-agent skip-on-error semantics of ``_install_one_agent``).
    """
    try:
        raw = src.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return rewrite_model_line(raw, client=client)
    except ValueError:
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
    dry_run: bool,
    on_progress: ProgressCallback,
    manifest_hashes: dict[str, str] | None,
    *,
    client: str = "claude-code",
) -> None:
    """Materialize one agent on update-project, resolving its ``model:`` tier.

    sub_5ctrrLJ root cause: update-project used to raw-``copy2`` bundled agents,
    re-introducing the unresolvable ``model: frontier`` token and breaking agent
    spawns after every upgrade. This routes the write through the SAME
    resolve-and-write path as fresh install (:func:`_install_one_agent`) so
    ``frontier`` becomes ``opus`` (etc.), while preserving genuinely user-edited
    agents via :func:`_is_user_modified`.
    """
    from ._init_project_skills import _install_one_agent

    framework_hashes = _framework_agent_hashes(agent_file, client=client)
    if _is_user_modified(dest, agent_file.name, manifest_hashes, framework_hashes=framework_hashes):
        logger.info("artifact_user_modified", path=str(dest))
        result.setdefault("modified", []).append(str(dest))
        return

    if dry_run:
        resolved = _render_agent(agent_file, client=client)
        if not dest.exists():
            result["created"].append(f"would create: {dest}")
        elif resolved is None or dest.read_text(encoding="utf-8") != resolved:
            result["updated"].append(f"would update: {dest}")
        return

    existed = dest.exists()
    error_count = len(result["errors"])
    _install_one_agent(agent_file, dest, force=True, result=result, on_progress=None, client=client)
    if len(result["errors"]) > error_count:
        if on_progress:
            on_progress("Error", str(dest))
        return
    # _install_one_agent always records "created"; on update an existing dest is
    # semantically an update, so reclassify for accurate reporting.
    if existed and str(dest) in result["created"]:
        result["created"].remove(str(dest))
        result.setdefault("updated", []).append(str(dest))
    if on_progress:
        on_progress("Updated" if existed else "Created", str(dest))
